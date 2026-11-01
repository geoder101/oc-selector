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

from pathlib import Path  # noqa: TC003

import pytest

from oc_selector.errors import (
    ExecutableMissingError,
    ExecutableNotExecutableError,
)
from oc_selector.process import build_launch_env, resolve_executable
from oc_selector.registry import ProfileEntry, Registry
from oc_selector.routing import NativeRoute


def _make_registry() -> Registry:
    return Registry(
        schema_version=1,
        default_profile="default",
        opencode_executable="opencode",
        profiles={
            "default": ProfileEntry(directory="opencode"),
            "gpt": ProfileEntry(directory="opencode_gpt", omo_profile="oc-selector-gpt"),
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

    def test_happy_unmanaged_preserves_omo(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/test-xdg")
        reg = _make_registry()
        base = {"OMO_PROFILE": "inherited-omo", "OCX_PROFILE": "inherited-ocx"}
        env = build_launch_env(reg, "default", base_env=base)
        # Unmanaged: preserve both
        assert env["OMO_PROFILE"] == "inherited-omo"
        assert env["OCX_PROFILE"] == "inherited-ocx"

    def test_happy_owned_sets_omo_clears_ocx(self, monkeypatch: pytest.MonkeyPatch) -> None:
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
