"""Read-only diagnostics collector for oc-selector.

C6 contract:
- ``info --json`` stable object with schema_version 1.
- Collect only allowlisted paths/versions/status/metadata.
- Never carry raw config, provider URLs, secret values or secret-reference paths.
- Exactly one version-only subprocess maximum (``[binary, '--version']``), 2s timeout.
- No npm, no bootstrap, no network.
- Unhealthy diagnostics exits 0 (issues are data, not errors).
- Human and JSON views share the exact same collector result.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from oc_selector import __version__
from oc_selector.paths import omo_config_path, registry_path, resolve_config_parent, state_root

if TYPE_CHECKING:
    from oc_selector.registry import Registry


@dataclass(frozen=True, slots=True)
class ProfileDiagnostics:
    """Per-profile health entry."""

    name: str
    directory: str
    omo_profile: str | None
    status: str = "ok"
    issues: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class DiagnosticsResult:
    """The full ``info --json`` output structure."""

    schema_version: int
    wrapper: dict[str, str]
    paths: dict[str, str]
    default_profile: str | None
    opencode: dict[str, str | None]
    profiles: list[ProfileDiagnostics]
    recovery: dict[str, Any]
    lock: dict[str, Any]
    warnings: list[dict[str, str]]

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict."""
        return {
            "schema_version": self.schema_version,
            "wrapper": self.wrapper,
            "paths": self.paths,
            "default_profile": self.default_profile,
            "opencode": self.opencode,
            "profiles": [
                {
                    "name": p.name,
                    "directory": p.directory,
                    "omo_profile": p.omo_profile,
                    "status": p.status,
                    "issues": p.issues,
                }
                for p in self.profiles
            ],
            "recovery": self.recovery,
            "lock": self.lock,
            "warnings": self.warnings,
        }


def probe_version(executable: str, timeout: float = 2.0) -> tuple[str | None, str]:  # noqa: PLR0911
    """Run ``[executable, '--version']`` with bounded timeout."""
    exe = Path(executable)
    if not exe.is_absolute():
        resolved = shutil.which(executable)
        if resolved is None:
            return None, "missing"
        exe = Path(resolved)
    if not exe.exists():
        return None, "missing"
    if not _is_executable(exe):
        return None, "not_executable"
    try:
        result = subprocess.run(  # noqa: S603
            [str(exe), "--version"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip(), "ok"
        return None, "ok"  # noqa: TRY300
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except OSError:
        return None, "missing"


def _is_executable(path: Path) -> bool:
    """Check if a path is an executable file."""
    try:
        st = path.stat()
        return stat.S_ISREG(st.st_mode) and os.access(path, os.X_OK)
    except OSError:
        return False


ProbeFn = Callable[[str, float], tuple[str | None, str]]


def collect_diagnostics(
    registry: Registry | None,
    *,
    version_probe: ProbeFn | None = None,
) -> DiagnosticsResult:
    """Collect all diagnostics into a single result."""
    if version_probe is None:
        version_probe = probe_version

    parent = resolve_config_parent()
    reg_path = registry_path()
    st_root = state_root()
    omo_path = omo_config_path()

    warnings: list[dict[str, str]] = []

    # Registry status
    default_profile: str | None = None
    profiles: list[ProfileDiagnostics] = []
    opencode_exe = "opencode"

    if registry is not None:
        default_profile = registry.default_profile
        opencode_exe = registry.opencode_executable
        for name, entry in registry.profiles.items():
            issues: list[str] = []
            if not (parent / entry.directory).exists():
                issues.append(f"directory-missing:{entry.directory}")
            profiles.append(
                ProfileDiagnostics(
                    name=name,
                    directory=entry.directory,
                    omo_profile=entry.omo_profile,
                    status="warning" if issues else "ok",
                    issues=issues,
                ),
            )
    else:
        warnings.append({"code": "registry-absent", "message": "Registry file not found or invalid"})

    # Version probe (only one subprocess call)
    version, ver_status = version_probe(opencode_exe, 2.0)

    # Lock status (read-only, no acquisition)
    lock_path = st_root / "maintenance.lock"
    lock_status = "absent"
    lock_info: dict[str, Any] = {"status": lock_status, "pid": None, "host": None, "started_at": None}
    if lock_path.exists():
        lock_status = "present"
        lock_info = {"status": lock_status, "pid": None, "host": None, "started_at": None}

    # Recovery status (read-only journal check)
    recovery_dir = st_root / "journals"
    recovery_status = "clean"
    transactions: list[dict[str, Any]] = []
    if recovery_dir.exists():
        journals = list(recovery_dir.iterdir())
        if journals:
            recovery_status = "pending"
            transactions.extend(
                {"id": j.name, "operation": "unknown", "phase": "unknown", "profiles": []} for j in journals
            )

    return DiagnosticsResult(
        schema_version=1,
        wrapper={"version": __version__},
        paths={
            "config_parent": str(parent),
            "registry": str(reg_path),
            "state": str(st_root),
            "omo_config": str(omo_path),
        },
        default_profile=default_profile,
        opencode={"path": opencode_exe, "version": version, "status": ver_status},
        profiles=profiles,
        recovery={"status": recovery_status, "transactions": transactions},
        lock=lock_info,
        warnings=warnings,
    )
