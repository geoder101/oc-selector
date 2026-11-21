"""TDD tests for raw dispatch and native process launcher.

Covers:
- Executable resolution (127/126)
- Environment building (OPENCODE_CONFIG_DIR, OMO_PROFILE, OCX_PROFILE)
- Unmanaged vs owned profile env handling
- Self-identity rejection
- Native tail preservation
- Dry-run creates no state files
- Windows branch (subprocess returncode pass-through, KeyboardInterrupt 130)
- POSIX branch (os.execve replaces a child process)
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from oc_selector.errors import (
    ExecutableMissingError,
    ExecutableNotExecutableError,
    MaintenanceError,
)
from oc_selector.process import build_launch_env, launch_native, resolve_executable
from oc_selector.registry import ProfileEntry, Registry
from oc_selector.routing import NativeRoute

if TYPE_CHECKING:
    from collections.abc import Callable

    from helpers.process_harness import ProcessHarness


@dataclass(frozen=True, slots=True)
class _FakeCompleted:
    """Stand-in for subprocess.CompletedProcess carrying only a returncode."""

    returncode: int


@dataclass
class _RunCapture:
    """Arguments observed by the fake subprocess.run or os.execve."""

    file: str = ""
    argv: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    check: bool = False


def _make_registry() -> Registry:
    return Registry(
        schema_version=1,
        default_profile="default",
        opencode_executable="opencode",
        profiles={
            "default": ProfileEntry(directory="opencode"),
            "gpt": ProfileEntry(
                directory="opencode_gpt",
                omo_profile="oc-selector-gpt",
            ),
            "local": ProfileEntry(directory="opencode_local"),
        },
    )


def _registry_for(exe: Path, directory: Path) -> Registry:
    return Registry(
        schema_version=1,
        default_profile="default",
        opencode_executable=str(exe),
        profiles={"default": ProfileEntry(directory=str(directory))},
    )


class TestResolveExecutable:
    def test_happy_absolute_path(self, tmp_path: Path) -> None:
        exe = tmp_path / "myexe"
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)
        result = resolve_executable(str(exe))
        assert result == exe

    def test_failure_missing(self) -> None:
        with pytest.raises(ExecutableMissingError):
            resolve_executable("/nonexistent/path/to/binary")

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX execute-bit semantics")
    def test_failure_not_executable(self, tmp_path: Path) -> None:
        f = tmp_path / "notexec"
        f.write_text("data")
        f.chmod(0o644)
        with pytest.raises(ExecutableNotExecutableError):
            resolve_executable(str(f))


class TestBuildLaunchEnv:
    def test_happy_sets_config_dir(self, isolated_home: Path) -> None:
        reg = _make_registry()
        env = build_launch_env(reg, "default", base_env={})
        assert env["OPENCODE_CONFIG_DIR"] == str(isolated_home / ".config" / "opencode")

    def test_happy_expands_tilde_config_dir(self, isolated_home: Path) -> None:
        """A ~-prefixed profile directory is expanded for OPENCODE_CONFIG_DIR."""
        reg = Registry(
            schema_version=1,
            default_profile="default",
            profiles={"default": ProfileEntry(directory="~/conf")},
        )
        env = build_launch_env(reg, "default", base_env={})
        assert env["OPENCODE_CONFIG_DIR"] == str(isolated_home / "conf")

    def test_happy_unmanaged_preserves_omo(
        self,
        isolated_home: Path,  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
    ) -> None:
        reg = _make_registry()
        base = {"OMO_PROFILE": "inherited-omo", "OCX_PROFILE": "inherited-ocx"}
        env = build_launch_env(reg, "default", base_env=base)
        # Unmanaged: preserve both
        assert env["OMO_PROFILE"] == "inherited-omo"
        assert env["OCX_PROFILE"] == "inherited-ocx"

    def test_happy_owned_sets_omo_clears_ocx(
        self,
        isolated_home: Path,  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
    ) -> None:
        reg = _make_registry()
        base = {"OMO_PROFILE": "old-omo", "OCX_PROFILE": "old-ocx"}
        env = build_launch_env(reg, "gpt", base_env=base)
        # Owned: set registered, clear OCX
        assert env["OMO_PROFILE"] == "oc-selector-gpt"
        assert "OCX_PROFILE" not in env

    def test_happy_preserves_other_vars(
        self,
        isolated_home: Path,  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
    ) -> None:
        reg = _make_registry()
        base = {"HOME": "/home/user", "OPENCODE_CONFIG": "custom", "PATH": "/usr/bin"}
        env = build_launch_env(reg, "default", base_env=base)
        assert env["HOME"] == "/home/user"
        assert env["OPENCODE_CONFIG"] == "custom"
        assert env["PATH"] == "/usr/bin"


class TestNativeRoute:
    def test_happy_tail_preserved(self) -> None:
        route = NativeRoute(argv=("run", "--flag", "value"), profile_name="gpt")
        assert route.argv == ("run", "--flag", "value")
        assert route.profile_name == "gpt"

    def test_happy_empty_tail(self) -> None:
        route = NativeRoute(argv=(), profile_name="default")
        assert route.argv == ()


class TestSelfRejection:
    def test_failure_self_identity(self, tmp_path: Path) -> None:
        from oc_selector.process import _reject_self  # pyright: ignore[reportPrivateUsage]  # noqa: PLC0415

        exe = tmp_path / "wrapper"
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)
        # This should not raise (different paths)
        _reject_self(exe)

    def test_failure_self_identity_raises(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An executable identical to the running wrapper is refused (exit 1)."""
        from oc_selector.process import _reject_self  # pyright: ignore[reportPrivateUsage]  # noqa: PLC0415

        exe = tmp_path / "wrapper"
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)
        monkeypatch.setattr(sys, "argv", [str(exe)])
        with pytest.raises(MaintenanceError) as excinfo:
            _reject_self(exe)
        assert excinfo.value.exit_code == 1

    def test_happy_resolve_oserror_swallowed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A resolve OSError is swallowed; identity checks cannot fail a launch."""
        from oc_selector.process import _reject_self  # pyright: ignore[reportPrivateUsage]  # noqa: PLC0415

        exe = tmp_path / "wrapper"
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)

        def _raise(_self: Path, *, _strict: bool = False) -> Path:
            raise OSError("simulated resolve failure")

        monkeypatch.setattr(Path, "resolve", _raise)
        _reject_self(exe)


class TestResolveExecutablePath:
    """PATH-based resolution of a relative executable name via shutil.which."""

    def test_happy_path_resolution(
        self,
        monkeypatch: pytest.MonkeyPatch,
        process_harness: ProcessHarness,
        fixture_executable: Callable[..., Path],
    ) -> None:
        """A name found on PATH resolves to the fixture executable path."""
        exe = fixture_executable("opencode")
        monkeypatch.setenv("PATH", str(process_harness.bin_dir))
        assert resolve_executable("opencode") == exe

    def test_failure_missing_in_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        process_harness: ProcessHarness,
    ) -> None:
        """A name absent from PATH raises the 127 missing-executable error."""
        monkeypatch.setenv("PATH", str(process_harness.bin_dir))
        with pytest.raises(ExecutableMissingError) as excinfo:
            resolve_executable("ocx-no-such-binary")
        assert excinfo.value.exit_code == 127

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX execute-bit semantics")
    def test_failure_found_but_not_executable(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """A PATH hit that fails the executable check raises the 126 error."""
        not_exec = tmp_path / "notexec"
        not_exec.write_text("data")
        not_exec.chmod(0o644)

        def _which(_cmd: str) -> str:
            return str(not_exec)

        monkeypatch.setattr("shutil.which", _which)
        with pytest.raises(ExecutableNotExecutableError) as excinfo:
            resolve_executable("notexec")
        assert excinfo.value.exit_code == 126


class TestIsExecutable:
    """The regular-file + X_OK check with OSError tolerance."""

    def test_happy_regular_executable(self, tmp_path: Path) -> None:
        """A regular executable file is recognized as executable."""
        from oc_selector.process import _is_executable  # pyright: ignore[reportPrivateUsage]  # noqa: PLC0415

        exe = tmp_path / "exe"
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)
        assert _is_executable(exe) is True

    def test_failure_stat_oserror_returns_false(self, tmp_path: Path) -> None:
        """A stat OSError (broken symlink) is tolerated as not-executable."""
        from oc_selector.process import _is_executable  # pyright: ignore[reportPrivateUsage]  # noqa: PLC0415

        broken = tmp_path / "broken"
        broken.symlink_to(tmp_path / "no-such-target")
        assert _is_executable(broken) is False


class TestLaunchNativeDryRun:
    """The dry-run entry: full setup (resolve, self-check, env, argv) without launch."""

    def _registry_for(self, exe: Path) -> Registry:
        return Registry(
            schema_version=1,
            default_profile="default",
            opencode_executable=str(exe),
            profiles={
                "default": ProfileEntry(directory="opencode"),
                "gpt": ProfileEntry(
                    directory="opencode_gpt",
                    omo_profile="oc-selector-gpt",
                ),
            },
        )

    def test_happy_dry_run_returns_zero_without_launch(
        self,
        isolated_home: Path,  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        process_harness: ProcessHarness,
        fixture_executable: Callable[..., Path],
    ) -> None:
        """dry_run=True runs the full setup and returns 0 without launching."""
        exe = fixture_executable("opencode")
        registry = self._registry_for(exe)
        route = NativeRoute(argv=("run", "--flag", "value"), profile_name="gpt")
        assert launch_native(route, registry, dry_run=True) == 0
        # The fixture executable records itself when run; dry-run must not run it.
        assert not (process_harness.records_dir / "opencode.record.json").exists()

    def test_happy_dry_run_falls_back_to_default_profile(
        self,
        isolated_home: Path,  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        process_harness: ProcessHarness,
        fixture_executable: Callable[..., Path],
    ) -> None:
        """A route without a profile name uses the registry default profile."""
        exe = fixture_executable("opencode")
        registry = self._registry_for(exe)
        route = NativeRoute(argv=(), profile_name=None)
        assert launch_native(route, registry, dry_run=True) == 0
        assert not (process_harness.records_dir / "opencode.record.json").exists()


class TestLaunchWindows:
    """The win32 branch: supervised subprocess with returncode pass-through."""

    def _install_fake_run(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        returncode: int,
        raise_keyboard: bool = False,
    ) -> _RunCapture:
        capture = _RunCapture()

        def _fake_run(
            argv: list[str],
            *,
            env: dict[str, str] | None = None,
            check: bool = False,
        ) -> _FakeCompleted:
            capture.argv = list(argv)
            capture.env = dict(env) if env is not None else {}
            capture.check = check
            if raise_keyboard:
                raise KeyboardInterrupt
            return _FakeCompleted(returncode=returncode)

        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(
            "oc_selector.process.subprocess",
            SimpleNamespace(run=_fake_run),
        )
        return capture

    def test_happy_win32_zero_returncode(
        self,
        isolated_home: Path,
        monkeypatch: pytest.MonkeyPatch,
        fixture_executable: Callable[..., Path],
    ) -> None:
        """A win32 launch with returncode 0 returns 0 and forwards argv/env."""
        exe = fixture_executable("opencode")
        directory = isolated_home / "opencode"
        directory.mkdir()
        registry = _registry_for(exe, directory)
        route = NativeRoute(argv=("run", "--flag", "value"), profile_name="default")
        capture = self._install_fake_run(monkeypatch, returncode=0)
        assert launch_native(route, registry) == 0
        assert capture.argv == [str(exe), "run", "--flag", "value"]
        assert capture.env["OPENCODE_CONFIG_DIR"] == str(directory)
        assert capture.check is False

    def test_happy_win32_nonzero_returncode_passthrough(
        self,
        isolated_home: Path,
        monkeypatch: pytest.MonkeyPatch,
        fixture_executable: Callable[..., Path],
    ) -> None:
        """A win32 launch passes the native returncode through unchanged."""
        exe = fixture_executable("opencode")
        directory = isolated_home / "opencode"
        directory.mkdir()
        registry = _registry_for(exe, directory)
        route = NativeRoute(argv=("run",), profile_name="default")
        _ = self._install_fake_run(monkeypatch, returncode=3)
        assert launch_native(route, registry) == 3

    def test_happy_win32_keyboard_interrupt_returns_130(
        self,
        isolated_home: Path,
        monkeypatch: pytest.MonkeyPatch,
        fixture_executable: Callable[..., Path],
    ) -> None:
        """A win32 launch interrupted by Ctrl+C returns 130."""
        exe = fixture_executable("opencode")
        directory = isolated_home / "opencode"
        directory.mkdir()
        registry = _registry_for(exe, directory)
        route = NativeRoute(argv=(), profile_name="default")
        _ = self._install_fake_run(monkeypatch, returncode=0, raise_keyboard=True)
        assert launch_native(route, registry) == 130


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX execve semantics")
class TestLaunchPosix:
    """The posix branch: os.execve replaces the calling process image."""

    def test_happy_posix_execve_replaces_child(
        self,
        isolated_home: Path,
        process_harness: ProcessHarness,
        fixture_executable: Callable[..., Path],
    ) -> None:
        """A non-win32 launch execves the fixture executable in a child process."""
        exe = fixture_executable("opencode", stdout="OCX-POSIX-MARKER\n")
        directory = isolated_home / "opencode"
        directory.mkdir()
        script = f"""\
import sys

from oc_selector.process import launch_native
from oc_selector.registry import ProfileEntry, Registry
from oc_selector.routing import NativeRoute

registry = Registry(
    schema_version=1,
    default_profile="default",
    opencode_executable={str(exe)!r},
    profiles={{"default": ProfileEntry(directory={str(directory)!r})}},
)
route = NativeRoute(argv=("some", "args"), profile_name="default")
sys.exit(launch_native(route, registry))
"""
        repo_root = Path(__file__).resolve().parent.parent
        completed = subprocess.run(
            [sys.executable, "-c", script],
            env=os.environ,
            cwd=str(repo_root),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0
        assert "OCX-POSIX-MARKER" in completed.stdout
        record = process_harness.record_for("opencode")
        assert record["argv"] == [str(exe), "some", "args"]

    def test_failure_posix_execve_oserror_propagates(
        self,
        isolated_home: Path,
        monkeypatch: pytest.MonkeyPatch,
        fixture_executable: Callable[..., Path],
    ) -> None:
        """A posix launch propagates the execve OSError after forwarding argv/env."""
        exe = fixture_executable("opencode")
        directory = isolated_home / "opencode"
        directory.mkdir()
        registry = _registry_for(exe, directory)
        route = NativeRoute(argv=("some", "args"), profile_name="default")
        capture = _RunCapture()

        def _fake_execve(file: str, args: list[str], env: dict[str, str]) -> None:
            capture.file = file
            capture.argv = list(args)
            capture.env = dict(env)
            raise OSError("simulated execve failure")

        monkeypatch.setattr("oc_selector.process.os.execve", _fake_execve)
        with pytest.raises(OSError, match="simulated execve failure"):
            launch_native(route, registry)
        assert capture.file == str(exe)
        assert capture.argv == [str(exe), "some", "args"]
        assert capture.env["OPENCODE_CONFIG_DIR"] == str(directory)
