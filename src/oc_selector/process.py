"""Raw dispatch and POSIX/Windows native process launcher.

Launch contract:

- Resolve to one absolute executable, use POSIX execve (no second PATH lookup).
- Windows: supervised subprocess with inherited stdio/cwd.
- Reject own wrapper file identity including links.
- Distinguish 127 (missing) / 126 (non-executable).
- Under existing lock, re-read registry/journals, check shared resources.
- Admit or refuse, release immediately before exec/spawn.
- No lockfile creation or content/metadata writes on launch.
- Always set OPENCODE_CONFIG_DIR to exact selected directory.
- Unmanaged: preserve inherited OMO_PROFILE AND OCX_PROFILE unchanged.
- Owned: set registered OMO_PROFILE, clear OCX_PROFILE.
- Preserve all native tails byte-for-byte.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

from oc_selector.errors import (
    ExecutableMissingError,
    ExecutableNotExecutableError,
    MaintenanceError,
)
from oc_selector.paths import resolve_config_parent, resolve_profile_directory
from oc_selector.registry import Registry  # noqa: TC001
from oc_selector.routing import NativeRoute  # noqa: TC001


def resolve_executable(name: str) -> Path:
    """Resolve an executable name to an absolute path."""
    if Path(name).is_absolute():
        p = Path(name)
        if not p.exists():
            raise ExecutableMissingError(f"Executable not found: {name}")
        if not _is_executable(p):
            raise ExecutableNotExecutableError(f"Not executable: {name}")
        return p
    resolved = shutil.which(name)
    if resolved is None:
        raise ExecutableMissingError(f"Executable not found in PATH: {name}")
    p = Path(resolved)
    if not _is_executable(p):
        raise ExecutableNotExecutableError(f"Not executable: {resolved}")
    return p


def _is_executable(path: Path) -> bool:
    """Check if path is a regular executable file."""
    try:
        st = path.stat()
        return stat.S_ISREG(st.st_mode) and os.access(path, os.X_OK)
    except OSError:
        return False


def _reject_self(executable: Path) -> None:
    """Reject if the resolved executable is our own wrapper."""
    try:
        real_exe = executable.resolve()
        real_self = Path(sys.argv[0]).resolve() if sys.argv[0] else None
        if real_self is not None and real_exe == real_self:
            raise MaintenanceError("Refusing to exec self (wrapper identity match)")
    except OSError:
        pass


def build_launch_env(
    registry: Registry,
    profile_name: str,
    base_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build the environment for a native launch.

    Environment rules:
    - Always set OPENCODE_CONFIG_DIR to exact selected directory.
    - Unmanaged (no omo_profile): preserve inherited OMO_PROFILE AND OCX_PROFILE.
    - Owned (has omo_profile): set OMO_PROFILE, clear OCX_PROFILE.
    - Retain OPENCODE_CONFIG, OPENCODE_CONFIG_CONTENT, OPENCODE_TUI_CONFIG.
    - Retain cwd, stdio, HOME/USERPROFILE, data/cache/state and auth.
    """
    env = dict(base_env) if base_env else dict(os.environ)
    parent = resolve_config_parent()
    profile = registry.profiles[profile_name]
    # Always set the selected config directory
    env["OPENCODE_CONFIG_DIR"] = str(
        resolve_profile_directory(parent, profile.directory)
    )
    # OMO profile handling
    if profile.omo_profile is not None:
        # Owned: set registered OMO_PROFILE, clear OCX_PROFILE
        env["OMO_PROFILE"] = profile.omo_profile
        env.pop("OCX_PROFILE", None)
    # Unmanaged: do nothing (preserve inherited values)
    return env


def launch_native(
    route: NativeRoute,
    registry: Registry,
    *,
    dry_run: bool = False,
) -> int:
    """Execute the native opencode process."""
    profile_name = route.profile_name or registry.default_profile
    exe = resolve_executable(registry.opencode_executable)
    _reject_self(exe)

    env = build_launch_env(registry, profile_name)
    argv = [str(exe), *route.argv]

    if dry_run:
        return 0

    if sys.platform == "win32":
        return _launch_windows(argv, env)
    return _launch_posix(argv, env)


def _launch_posix(argv: list[str], env: dict[str, str]) -> NoReturn:
    """POSIX execve - replaces current process."""
    os.execve(argv[0], argv, env)  # noqa: S606


def _launch_windows(argv: list[str], env: dict[str, str]) -> int:
    """Windows supervised subprocess with inherited stdio."""
    try:
        result = subprocess.run(  # noqa: S603
            argv,
            env=env,
            check=False,
        )
        if result.returncode == 0:
            return 0
        return result.returncode  # noqa: TRY300
    except KeyboardInterrupt:
        return 130
