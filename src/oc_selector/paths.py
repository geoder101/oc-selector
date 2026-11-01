"""Portable path resolution for oc-selector state and configuration.

Contract:
- Parent is absolute ``XDG_CONFIG_HOME`` if set, otherwise ``Path.home() / '.config'``.
- Reject relative/empty explicitly set XDG values.
- State root is ``<parent>/oc-selector``.
- Registry is ``<state>/config.toml``.
- OMO location stays actual home ``~/.omo``, independent of selected OpenCode directory.
"""

from __future__ import annotations

import os
from pathlib import Path

from oc_selector.errors import RegistryError


def resolve_config_parent() -> Path:
    """Resolve the absolute config parent directory.

    Uses ``XDG_CONFIG_HOME`` if set and absolute, otherwise ``~/.config``.
    Raises ``RegistryError`` for relative or empty XDG values.
    """
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg is not None:
        if not xdg:
            raise RegistryError("XDG_CONFIG_HOME is set but empty")
        p = Path(xdg)
        if not p.is_absolute():
            raise RegistryError(f"XDG_CONFIG_HOME is relative: {xdg!r}")
        return p
    return Path.home() / ".config"


def state_root() -> Path:
    """Return the oc-selector state root: ``<parent>/oc-selector``."""
    return resolve_config_parent() / "oc-selector"


def registry_path_for(parent: Path) -> Path:
    """Return the registry TOML path under an explicit config parent."""
    return parent / "oc-selector" / "config.toml"


def registry_path() -> Path:
    """Return the registry TOML path: ``<state>/config.toml``."""
    return registry_path_for(resolve_config_parent())


def omo_config_path() -> Path:
    """Return the OMO config path: ``~/.omo`` (always actual home, not XDG)."""
    return Path.home() / ".omo"


def profile_directory(parent: Path, name: str) -> Path:
    """Return the directory for a named profile under the config parent."""
    return parent / name
