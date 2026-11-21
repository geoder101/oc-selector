"""Read-only native launch admission using the existing maintenance lock."""

from __future__ import annotations

import contextlib
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

import portalocker

from oc_selector.errors import MaintenanceError
from oc_selector.transactions import find_pending_journals


@contextlib.contextmanager
def launch_admission(root: Path) -> Generator[None]:
    """Hold an existing lock while inspecting launch inputs; never create it."""
    lock_path = root / "maintenance.lock"
    with contextlib.ExitStack() as stack:
        try:
            stream = stack.enter_context(
                lock_path.open("rb+" if os.name == "nt" else "rb")
            )
        except FileNotFoundError:
            stream = None
        except OSError as exc:
            raise MaintenanceError(f"Cannot inspect maintenance lock: {exc}") from exc
        if stream is not None:
            try:
                portalocker.lock(stream, portalocker.LOCK_EX | portalocker.LOCK_NB)
            except portalocker.LockException:
                raise MaintenanceError("Maintenance is in progress") from None
            stack.callback(portalocker.unlock, stream)
        if find_pending_journals(root):
            raise MaintenanceError("Pending recovery; run 'oc-selector init --recover'")
        yield
