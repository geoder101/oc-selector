"""Shared pytest fixtures for oc-selector tests.

Every fixture isolates the test environment from the developer's real home,
configuration, PATH and network. No test may touch the real home directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from helpers.process_harness import ProcessHarness

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

# Capture the real home before any fixture mutates the environment.
_REAL_HOME = Path.home()

# Dead loopback endpoint; nothing listens, so accidental HTTP fails on loopback.
_LOOPBACK_PROXY = "http://127.0.0.1:9"


class HomeGuard:
    """Guard that detects attempts to touch the real home directory."""

    real_home: Path
    isolated_home: Path

    def __init__(self, real_home: Path, isolated_home: Path) -> None:
        """Record the real and isolated home roots."""
        self.real_home = real_home
        self.isolated_home = isolated_home

    def assert_isolated(self, path: Path) -> None:
        """Assert ``path`` is within the isolated home and not the real home."""
        resolved = path.resolve()
        assert resolved.is_relative_to(self.isolated_home.resolve()), f"path {path} escapes the isolated home"
        assert not resolved.is_relative_to(self.real_home.resolve()), f"path {path} touches the real home"


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate HOME/USERPROFILE/XDG_CONFIG_HOME/cwd and the network.

    Returns:
        The isolated home directory.

    """
    home = tmp_path / "home"
    home.mkdir()
    config_home = home / ".config"
    config_home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("http_proxy", _LOOPBACK_PROXY)
    monkeypatch.setenv("https_proxy", _LOOPBACK_PROXY)
    monkeypatch.setenv("HTTP_PROXY", _LOOPBACK_PROXY)
    monkeypatch.setenv("HTTPS_PROXY", _LOOPBACK_PROXY)
    monkeypatch.setenv("no_proxy", "")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.chdir(home)
    return home


@pytest.fixture
def deny_personal_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> HomeGuard:
    """Isolate the home and provide a guard that detects real-home access."""
    isolated = tmp_path / "isolated-home"
    isolated.mkdir()
    monkeypatch.setenv("HOME", str(isolated))
    monkeypatch.setenv("USERPROFILE", str(isolated))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(isolated / ".config"))
    monkeypatch.chdir(isolated)
    return HomeGuard(real_home=_REAL_HOME, isolated_home=isolated)


@pytest.fixture
def process_harness() -> Iterator[ProcessHarness]:
    """Provide an isolated subprocess harness sandbox."""
    harness = ProcessHarness()
    _ = harness.__enter__()
    yield harness
    harness.__exit__(None, None, None)


@pytest.fixture
def fixture_executable(process_harness: ProcessHarness) -> Callable[..., Path]:
    """Create fake executables that record argv/stdin/output/status."""

    def factory(
        name: str,
        *,
        stdout: str = "",
        stderr: str = "",
        exit_code: int = 0,
    ) -> Path:
        return process_harness.create_executable(
            name,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
        )

    return factory
