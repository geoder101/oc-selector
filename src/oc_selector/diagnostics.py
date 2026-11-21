"""Read-only diagnostics collector for oc-selector.

Contract:
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
from typing import TYPE_CHECKING

from oc_selector import __version__
from oc_selector.agents_link import is_identical_symlink, link_path
from oc_selector.paths import (
    display_profile_directory,
    global_agents_md_path,
    omo_config_path,
    registry_path,
    resolve_config_parent,
    resolve_profile_directory,
    state_root,
)
from oc_selector.transactions import JournalPhase, read_journal

if TYPE_CHECKING:
    from oc_selector.registry import Registry


@dataclass(frozen=True, slots=True)
class ProfileDiagnostics:
    """Per-profile health entry."""

    name: str
    # Resolved, home-abbreviated directory (see display_profile_directory).
    directory: str
    omo_profile: str | None
    status: str = "ok"
    issues: list[str] = field(default_factory=list)
    # Per-profile ``AGENTS.md`` link status: ``"linked-ok"``, ``"dangling"`` or
    # ``"absent"`` (see collect_diagnostics). Defaults to ``"absent"``.
    agents_link: str = "absent"


@dataclass(frozen=True, slots=True)
class DiagnosticsResult:
    """The full ``info --json`` output structure."""

    schema_version: int
    wrapper: dict[str, str]
    paths: dict[str, str]
    default_profile: str | None
    opencode: dict[str, str | None]
    profiles: list[ProfileDiagnostics]
    recovery: dict[str, object]
    lock: dict[str, object]
    warnings: list[dict[str, str]]

    def to_dict(self) -> dict[str, object]:
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
                    "agents_link": p.agents_link,
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


def _agents_link_status(profile_dir: Path, global_path: Path) -> str:
    """Return the per-profile ``AGENTS.md`` link status.

    ``"linked-ok"`` when the link is an identical symlink to the global guard;
    ``"dangling"`` when the link is a symlink whose target is absent;
    ``"absent"`` otherwise (no link, or a real non-symlink file).
    """
    link = link_path(profile_dir)
    if not link.is_symlink():
        return "absent"
    if not global_path.exists():
        return "dangling"
    return "linked-ok" if is_identical_symlink(link, global_path) else "absent"


def _recovery_status(root: Path) -> tuple[str, list[dict[str, object]]]:
    """Classify journal phases without changing recovery evidence."""
    status = "clean"
    transactions: list[dict[str, object]] = []
    for path in (root / "journals").glob("*.json"):
        journal = read_journal(path)
        if journal is not None and journal.phase == JournalPhase.CLEANUP_COMPLETE:
            continue
        if journal is None:
            status = "corrupt"
        elif status != "corrupt":
            status = "pending"
        transactions.append(
            {
                "id": journal.id if journal else path.name,
                "operation": journal.operation if journal else "unknown",
                "phase": journal.phase.value if journal else "corrupt",
                "profiles": journal.affected_profiles if journal else [],
            }
        )
    return status, transactions


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
        global_path = global_agents_md_path()
        for name, entry in registry.profiles.items():
            issues: list[str] = []
            if not resolve_profile_directory(parent, entry.directory).exists():
                issues.append(f"directory-missing:{entry.directory}")
            profiles.append(
                ProfileDiagnostics(
                    name=name,
                    directory=display_profile_directory(parent, entry.directory),
                    omo_profile=entry.omo_profile,
                    agents_link=_agents_link_status(
                        resolve_profile_directory(parent, entry.directory),
                        global_path,
                    ),
                    status="warning" if issues else "ok",
                    issues=issues,
                ),
            )
    else:
        warnings.append(
            {
                "code": "registry-absent",
                "message": "Registry file not found or invalid",
            },
        )

    # Version probe (only one subprocess call)
    version, ver_status = version_probe(opencode_exe, 2.0)

    # Lock status (read-only, no acquisition)
    lock_path = st_root / "maintenance.lock"
    lock_status = "absent"
    lock_info: dict[str, object] = {
        "status": lock_status,
        "pid": None,
        "host": None,
        "started_at": None,
    }
    if lock_path.exists():
        lock_status = "present"
        lock_info = {
            "status": lock_status,
            "pid": None,
            "host": None,
            "started_at": None,
        }

    # Recovery status (read-only journal check)
    recovery_status, transactions = _recovery_status(st_root)

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
