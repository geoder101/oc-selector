"""Container-based e2e for the generated completion scripts.

Marker-gated (``container``). Runs the generated zsh/fish scripts in a real
podman container and drives the actual shells via tmux, asserting that
management switches, profiles, and native delegation all complete correctly.

Building the image is the one-time, opt-in network step
(``just completion-e2e-image``). The test run itself is network-free: it uses a
prebuilt image, stub executables, and ``--network none``. It skips cleanly when
podman or the image is unavailable (so the default suite stays green).
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from oc_selector.completion import generate_completion
from oc_selector.registry import ProfileEntry, Registry

pytestmark = pytest.mark.container

IMAGE = "oc-selector-completion-e2e:latest"
E2E_ROOT: Path = Path(__file__).resolve().parent / "e2e"

_OPTIONS: dict[str, list[str]] = {
    "list": ["--json"],
    "init": ["--no-backup", "--dry-run", "--recover"],
    "create": ["--model", "-m", "--omo-profile", "--no-interactive", "--dry-run"],
    "edit": ["--directory", "-d", "--model", "-m", "--omo-profile", "--dry-run"],
    "delete": ["--dry-run"],
    "set-default": ["--dry-run"],
    "sync": ["--all", "--dry-run"],
    "upgrade-omo": ["--version", "--all", "--dry-run"],
    "completion": ["--alias"],
    "help": ["--version"],
    "info": ["--json"],
}

_OPENCODE_STUB = """#!/bin/sh
if [ "${1:-}" = "--get-yargs-completions" ]; then
  shift
  echo '$0'
  case "${1:-}" in
    run) printf '%s\\n' --model --session --agent; exit 0 ;;
    serve) printf '%s\\n' --port --hostname; exit 0 ;;
  esac
  printf '%s\\n' run serve mcp agent
  exit 0
fi
exit 0
"""

_CASES: tuple[str, ...] = (
    "zsh create",
    "zsh run",
    "zsh first",
    "fish create",
    "fish run",
    "fish first",
)


def _podman() -> list[str]:
    """Return the podman invocation (overridable for sandboxed hosts)."""
    return shlex.split(os.environ.get("OC_SELECTOR_E2E_PODMAN", "podman"))


def _registry() -> Registry:
    return Registry(
        schema_version=1,
        default_profile="default",
        opencode_executable="opencode",
        profiles={
            "default": ProfileEntry(directory="opencode"),
            "gpt": ProfileEntry(directory="opencode_gpt"),
        },
    )


def _stage(staging: Path) -> None:
    """Write the generated scripts and stub executables into ``staging``."""
    bin_dir = staging / "bin"
    bin_dir.mkdir(parents=True)
    (staging / "zsh_completion.zsh").write_text(
        generate_completion("zsh", _registry(), _OPTIONS),
        encoding="utf-8",
    )
    (staging / "fish_completion.fish").write_text(
        generate_completion("fish", _registry(), _OPTIONS),
        encoding="utf-8",
    )
    selector = bin_dir / "oc-selector"
    selector.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    selector.chmod(0o755)
    opencode = bin_dir / "opencode"
    opencode.write_text(_OPENCODE_STUB, encoding="utf-8")
    opencode.chmod(0o755)


def _podman_available() -> bool:
    return (
        bool(os.environ.get("OC_SELECTOR_E2E_PODMAN"))
        or shutil.which("podman") is not None
    )


def _image_built() -> bool:
    proc = subprocess.run(
        [*_podman(), "image", "exists", IMAGE],
        capture_output=True,
        check=False,
    )
    return proc.returncode == 0


@pytest.mark.container
def test_completion_e2e(tmp_path: Path) -> None:
    """Drive real zsh/fish in a container and assert every case completes."""
    if not _podman_available():
        pytest.skip("podman not available; install it or set OC_SELECTOR_E2E_PODMAN")
    if not _image_built():
        pytest.skip("e2e image not built; run `just completion-e2e-image` first")

    staging = tmp_path / "stage"
    _stage(staging)
    cmd = [
        *_podman(),
        "run",
        "--rm",
        "--network",
        "none",
        "-v",
        f"{staging}:/work:Z",
        "-v",
        f"{E2E_ROOT}:/e2e:Z",
        IMAGE,
        "bash",
        "/e2e/runner.sh",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=240)
    failures = [line for line in proc.stdout.splitlines() if line.endswith(" FAIL")]
    detail = proc.stdout.strip()
    if proc.stderr:
        detail += "\n[stderr]\n" + proc.stderr
    assert proc.returncode == 0, f"runner exited non-zero:\n{detail}"
    assert not failures, f"e2e cases failed:\n{detail}"
    for case in _CASES:
        assert f"{case} PASS" in proc.stdout, f"missing PASS for {case}:\n{detail}"
