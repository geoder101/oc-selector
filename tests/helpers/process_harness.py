"""Isolated subprocess test harness.

This module provides :class:`ProcessHarness`, the shared infrastructure used by
the test suite to exercise child processes in a fully isolated environment.

Guarantees:

- Disposable ``HOME``/``USERPROFILE``/``XDG_CONFIG_HOME``/``cwd`` under a temp
  root that is removed on exit.
- Fixture executables that record their ``argv`` and ``stdin`` to a JSON file and
  emit configured ``stdout``/``stderr``/exit status.
- A loopback-only network guard: child processes are given proxy environment
  variables pointing at a dead loopback endpoint, so accidental HTTP cannot reach
  the real network.
- A controlled ``PATH`` that only contains the fixture executable directory.
- Every subprocess result is recorded to a JSON file for evidence.
- Paths containing spaces are handled because all subprocess calls use argument
  lists (never a shell).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Self

from pydantic import TypeAdapter

if TYPE_CHECKING:
    from collections.abc import Sequence

_RECORD_ADAPTER: TypeAdapter[dict[str, object]] = TypeAdapter(dict[str, object])

# Dead loopback endpoint (port 9 / "discard"); nothing listens, so any HTTP client
# that honors proxy env vars fails fast on loopback instead of reaching the network.
_LOOPBACK_PROXY = "http://127.0.0.1:9"

# Body of the generated fixture executable. ``__OCX_CONFIG_PATH__`` is replaced
# with the repr of the per-executable config path before the script is written.
_FIXTURE_TEMPLATE = """\
import json
import sys

CONFIG_PATH = __OCX_CONFIG_PATH__


def main() -> None:
    with open(CONFIG_PATH, encoding="utf-8") as handle:
        config = json.load(handle)
    stdin_data = sys.stdin.read()
    sys.stdout.write(config["stdout"])
    sys.stderr.write(config["stderr"])
    record = {
        "argv": sys.argv,
        "stdin": stdin_data,
        "stdout": config["stdout"],
        "stderr": config["stderr"],
        "exit_code": config["exit_code"],
    }
    with open(config["record_path"], "w", encoding="utf-8") as handle:
        json.dump(record, handle)
    sys.exit(config["exit_code"])


if __name__ == "__main__":
    main()
"""


@dataclass(frozen=True)
class ProcessResult:
    """Captured outcome of a single subprocess invocation."""

    argv: tuple[str, ...]
    stdin: str
    stdout: str
    stderr: str
    exit_code: int
    record_path: Path

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable representation of the result."""
        return {
            "argv": list(self.argv),
            "stdin": self.stdin,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "record_path": str(self.record_path),
        }


class ProcessHarness:
    """Context manager providing an isolated subprocess sandbox.

    Usage::

        with ProcessHarness() as harness:
            exe = harness.create_executable("opencode", stdout="ok")
            result = harness.run([exe, "run", "--flag"], stdin="hello")
            assert result.exit_code == 0
    """

    _tmp: tempfile.TemporaryDirectory[str]
    root: Path
    home: Path
    bin_dir: Path
    records_dir: Path
    cwd: Path

    def __init__(self, prefix: str = "ocx-harness-") -> None:
        """Create the isolated sandbox root and its subdirectories.

        Args:
            prefix: Prefix for the temporary root directory. May include spaces
                to exercise path handling.

        """
        self._tmp = tempfile.TemporaryDirectory(prefix=prefix)
        self.root = Path(self._tmp.name)
        self.home = self.root / "home"
        self.bin_dir = self.root / "bin"
        self.records_dir = self.root / "records"
        self.cwd = self.root / "cwd"
        for directory in (self.home, self.bin_dir, self.records_dir, self.cwd):
            directory.mkdir(parents=True, exist_ok=True)
        self._results: list[ProcessResult] = []

    def __enter__(self) -> Self:
        """Enter the context, returning the harness itself."""
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        """Exit the context, removing the temporary sandbox."""
        self._tmp.cleanup()

    @property
    def results(self) -> list[ProcessResult]:
        """Return all recorded subprocess results, in invocation order."""
        return list(self._results)

    def isolated_env(self) -> dict[str, str]:
        """Return the isolated environment applied to every child process."""
        return {
            "HOME": str(self.home),
            "USERPROFILE": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "PATH": str(self.bin_dir),
            "http_proxy": _LOOPBACK_PROXY,
            "https_proxy": _LOOPBACK_PROXY,
            "HTTP_PROXY": _LOOPBACK_PROXY,
            "HTTPS_PROXY": _LOOPBACK_PROXY,
            "ALL_PROXY": _LOOPBACK_PROXY,
            "no_proxy": "",
            "NO_PROXY": "",
        }

    def create_executable(
        self,
        name: str,
        *,
        stdout: str = "",
        stderr: str = "",
        exit_code: int = 0,
    ) -> Path:
        """Create a fixture executable that records argv/stdin and emits output.

        Args:
            name: Executable name (also the file name under the bin directory).
            stdout: Text the executable writes to stdout.
            stderr: Text the executable writes to stderr.
            exit_code: Exit status the executable returns.

        Returns:
            Absolute path to the created executable.

        """
        config_path = self.records_dir / f"{name}.config.json"
        record_path = self.records_dir / f"{name}.record.json"
        config = {
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
            "record_path": str(record_path),
        }
        _ = config_path.write_text(json.dumps(config), encoding="utf-8")
        body = _FIXTURE_TEMPLATE.replace("__OCX_CONFIG_PATH__", repr(str(config_path)))
        if os.name == "posix":
            script_path = self.bin_dir / name
            _ = script_path.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
            script_path.chmod(0o700)
            return script_path
        # Windows: a .bat shim invokes the Python body with all forwarded args.
        py_path = self.bin_dir / f"{name}.py"
        _ = py_path.write_text(body, encoding="utf-8")
        bat_path = self.bin_dir / f"{name}.bat"
        _ = bat_path.write_text(f'@echo off\r\n"{sys.executable}" "{py_path}" %*\r\n', encoding="utf-8")
        return bat_path

    def run(
        self,
        argv: Sequence[str | Path],
        *,
        stdin: str = "",
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
    ) -> ProcessResult:
        """Run a subprocess in the isolated environment and record the result.

        Args:
            argv: Command and arguments (first element is the executable).
            stdin: Text written to the child's stdin.
            env: Extra environment variables merged over the isolated env.
            cwd: Working directory; defaults to the harness cwd.

        Returns:
            The captured :class:`ProcessResult`.

        """
        full_env = self.isolated_env()
        if env is not None:
            full_env.update(env)
        run_cwd = cwd if cwd is not None else self.cwd
        completed = subprocess.run(
            [str(item) for item in argv],
            input=stdin,
            capture_output=True,
            text=True,
            env=full_env,
            cwd=str(run_cwd),
            check=False,
        )
        record_path = self.records_dir / f"run-{len(self._results)}.json"
        result = ProcessResult(
            argv=tuple(str(item) for item in argv),
            stdin=stdin,
            stdout=completed.stdout,
            stderr=completed.stderr,
            exit_code=completed.returncode,
            record_path=record_path,
        )
        _ = record_path.write_text(json.dumps(result.to_dict(), indent=2) + "\n", encoding="utf-8")
        self._results.append(result)
        return result

    def record_for(self, name: str) -> dict[str, object]:
        """Return the self-record written by a fixture executable.

        Args:
            name: The executable name passed to :meth:`create_executable`.

        Returns:
            The recorded argv/stdin/stdout/stderr/exit_code mapping.

        """
        record_path = self.records_dir / f"{name}.record.json"
        return _RECORD_ADAPTER.validate_python(json.loads(record_path.read_text(encoding="utf-8")))
