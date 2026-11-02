"""TDD tests for raw dispatch and native process launcher.

Covers:
- Executable resolution (127/126)
- Environment building (OPENCODE_CONFIG_DIR, OMO_PROFILE, OCX_PROFILE)
- Unmanaged vs owned profile env handling
- Self-identity rejection
- Native tail preservation
- Dry-run creates no state files
"""

from __future__ import annotations

import sys
from pathlib import Path
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

    def test_failure_not_executable(self, tmp_path: Path) -> None:
        f = tmp_path / "notexec"
        f.write_text("data")
        f.chmod(0o644)
        with pytest.raises(ExecutableNotExecutableError):
            resolve_executable(str(f))


class TestBuildLaunchEnv:
    def test_happy_sets_config_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/test-xdg")
        reg = _make_registry()
        env = build_launch_env(reg, "default", base_env={})
        assert env["OPENCODE_CONFIG_DIR"] == "/tmp/test-xdg/opencode"

    def test_happy_unmanaged_preserves_omo(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/test-xdg")
        reg = _make_registry()
        base = {"OMO_PROFILE": "inherited-omo", "OCX_PROFILE": "inherited-ocx"}
        env = build_launch_env(reg, "default", base_env=base)
        # Unmanaged: preserve both
        assert env["OMO_PROFILE"] == "inherited-omo"
        assert env["OCX_PROFILE"] == "inherited-ocx"

    def test_happy_owned_sets_omo_clears_ocx(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/test-xdg")
        reg = _make_registry()
        base = {"OMO_PROFILE": "old-omo", "OCX_PROFILE": "old-ocx"}
        env = build_launch_env(reg, "gpt", base_env=base)
        # Owned: set registered, clear OCX
        assert env["OMO_PROFILE"] == "oc-selector-gpt"
        assert "OCX_PROFILE" not in env

    def test_happy_preserves_other_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/test-xdg")
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
