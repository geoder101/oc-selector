"""TDD tests for Typer management CLI and raw dispatch.

Covers C1/C2/C4 contracts:
- CLI routes management commands
- Raw dispatch forwards args to native
- Error handling with proper exit codes
- No silent fallbacks
"""

from __future__ import annotations

from pathlib import Path  # noqa: TC003

import pytest
from typer.testing import CliRunner

from oc_selector.cli import app
from oc_selector.registry import ProfileEntry, Registry, registry_to_toml

runner = CliRunner()


@pytest.fixture
def setup_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set up a registry and patch environment for isolated paths."""
    config_dir = tmp_path / "xdg-config"
    config_dir.mkdir(parents=True)
    reg = Registry(
        default_profile="default",
        profiles={"default": ProfileEntry(directory="default")},
    )
    reg_path = config_dir / "config.toml"
    reg_path.write_text(registry_to_toml(reg))

    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_dir))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    return reg_path


class TestInitCommand:
    def test_happy_init(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        assert "Created" in result.output or "Already" in result.output


class TestListCommand:
    def test_happy_list(self, setup_registry: Path) -> None:  # noqa: ARG002
        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "default" in result.output

    def test_failure_no_registry(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "nonexistent"))
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

        result = runner.invoke(app, ["list"])
        assert result.exit_code == 1
        assert "No registry" in result.output


class TestCreateCommand:
    def test_happy_create(self, setup_registry: Path) -> None:  # noqa: ARG002
        result = runner.invoke(app, ["create", "test-profile", "test-dir"])
        assert result.exit_code == 0
        assert "Created" in result.output

    def test_failure_duplicate(self, setup_registry: Path) -> None:  # noqa: ARG002
        result = runner.invoke(app, ["create", "default", "other-dir"])
        assert result.exit_code == 1
        assert "already exists" in result.output


class TestSetDefaultCommand:
    def test_happy_set_default(self, setup_registry: Path) -> None:  # noqa: ARG002
        # Create a second profile first
        runner.invoke(app, ["create", "second", "second-dir"])
        result = runner.invoke(app, ["set-default", "second"])
        assert result.exit_code == 0
        assert "Set default" in result.output

    def test_failure_not_found(self, setup_registry: Path) -> None:  # noqa: ARG002
        result = runner.invoke(app, ["set-default", "nonexistent"])
        assert result.exit_code == 1
        assert "not found" in result.output


class TestCompletionCommand:
    def test_happy_bash(self, setup_registry: Path) -> None:  # noqa: ARG002
        result = runner.invoke(app, ["completion", "bash"])
        assert result.exit_code == 0
        assert "oc-selector" in result.output

    def test_happy_zsh(self, setup_registry: Path) -> None:  # noqa: ARG002
        result = runner.invoke(app, ["completion", "zsh"])
        assert result.exit_code == 0

    def test_happy_fish(self, setup_registry: Path) -> None:  # noqa: ARG002
        result = runner.invoke(app, ["completion", "fish"])
        assert result.exit_code == 0

    def test_happy_powershell(self, setup_registry: Path) -> None:  # noqa: ARG002
        result = runner.invoke(app, ["completion", "powershell"])
        assert result.exit_code == 0


class TestRecoverCommand:
    def test_happy_no_pending(self, setup_registry: Path) -> None:  # noqa: ARG002
        result = runner.invoke(app, ["recover"])
        assert result.exit_code == 0
        assert "No pending" in result.output
