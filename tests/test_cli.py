"""TDD tests for Typer management CLI and raw dispatch.

Covers:
- CLI routes management commands
- Raw dispatch forwards args to native
- Error handling with proper exit codes
- No silent fallbacks
"""

from __future__ import annotations

import json
from pathlib import Path  # noqa: TC003

import pytest
from pydantic import TypeAdapter
from typer.testing import CliRunner

from oc_selector import __version__
from oc_selector.cli import app
from oc_selector.paths import resolve_config_parent
from oc_selector.registry import ProfileEntry, Registry, load_registry, registry_to_toml

runner = CliRunner()

_PAYLOAD_ADAPTER: TypeAdapter[dict[str, object]] = TypeAdapter(dict[str, object])
_PROFILES_ADAPTER: TypeAdapter[list[dict[str, object]]] = TypeAdapter(
    list[dict[str, object]],
)


@pytest.fixture
def setup_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set up a registry and patch environment for isolated paths."""
    config_dir = tmp_path / "xdg-config"
    config_dir.mkdir(parents=True)
    reg = Registry(
        default_profile="default",
        profiles={"default": ProfileEntry(directory="default")},
    )
    reg_path = config_dir / "oc-selector" / "config.toml"
    reg_path.parent.mkdir(parents=True, exist_ok=True)
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
    def test_happy_list(self, setup_registry: Path) -> None:  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "default" in result.output

    def test_failure_no_registry(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "nonexistent"))
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

        result = runner.invoke(app, ["list"])
        assert result.exit_code == 2
        assert "No registry" in result.output


class TestCreateCommand:
    def test_happy_create(self, setup_registry: Path) -> None:  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        result = runner.invoke(app, ["create", "test-profile", "test-dir"])
        assert result.exit_code == 0
        assert "Created" in result.output

    def test_failure_duplicate(self, setup_registry: Path) -> None:  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        result = runner.invoke(app, ["create", "default", "other-dir"])
        assert result.exit_code == 1
        assert "already exists" in result.output


class TestSetDefaultCommand:
    def test_happy_set_default(self, setup_registry: Path) -> None:  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        # Create a second profile first
        runner.invoke(app, ["create", "second", "second-dir"])
        result = runner.invoke(app, ["set-default", "second"])
        assert result.exit_code == 0
        assert "Set default" in result.output

    def test_failure_not_found(self, setup_registry: Path) -> None:  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        result = runner.invoke(app, ["set-default", "nonexistent"])
        assert result.exit_code == 1
        assert "not found" in result.output


class TestCompletionCommand:
    def test_happy_bash(self, setup_registry: Path) -> None:  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        result = runner.invoke(app, ["completion", "bash"])
        assert result.exit_code == 0
        assert "oc-selector" in result.output

    def test_happy_zsh(self, setup_registry: Path) -> None:  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        result = runner.invoke(app, ["completion", "zsh"])
        assert result.exit_code == 0

    def test_happy_fish(self, setup_registry: Path) -> None:  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        result = runner.invoke(app, ["completion", "fish"])
        assert result.exit_code == 0

    def test_happy_powershell(self, setup_registry: Path) -> None:  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        result = runner.invoke(app, ["completion", "powershell"])
        assert result.exit_code == 0


class TestRecoverCommand:
    def test_happy_no_pending(self, setup_registry: Path) -> None:  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        result = runner.invoke(app, ["init", "--recover"])
        assert result.exit_code == 0
        assert "No pending" in result.output


class TestListJson:
    def test_happy_list_json(self, setup_registry: Path) -> None:  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        result = runner.invoke(app, ["list", "--json"])
        assert result.exit_code == 0
        payload = _PAYLOAD_ADAPTER.validate_python(json.loads(result.output))
        assert payload["schema_version"] == 1
        assert payload["default_profile"] == "default"
        profiles = _PROFILES_ADAPTER.validate_python(payload["profiles"])
        assert any(p["name"] == "default" for p in profiles)


class TestInfoCommand:
    def test_happy_info(self, setup_registry: Path) -> None:  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        result = runner.invoke(app, ["info"])
        assert result.exit_code == 0
        assert "OpenCode:" in result.output

    def test_happy_info_json(self, setup_registry: Path) -> None:  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        result = runner.invoke(app, ["info", "--json"])
        assert result.exit_code == 0
        payload = _PAYLOAD_ADAPTER.validate_python(json.loads(result.output))
        assert payload["schema_version"] == 1
        assert "profiles" in payload


class TestHelpCommand:
    def test_happy_help_grammar(self, setup_registry: Path) -> None:  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        result = runner.invoke(app, ["help"])
        assert result.exit_code == 0
        assert "list [--json]" in result.output
        assert "upgrade-omo" in result.output

    def test_happy_help_version(self, setup_registry: Path) -> None:  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        result = runner.invoke(app, ["help", "--version"])
        assert result.exit_code == 0
        assert result.output.strip() == __version__


class TestCompletionAlias:
    def test_happy_alias(self, setup_registry: Path) -> None:  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        result = runner.invoke(app, ["completion", "bash", "--alias", "oc"])
        assert result.exit_code == 0
        assert "_oc_completions" in result.output


class TestInitFlags:
    def test_happy_init_dry_run(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        result = runner.invoke(app, ["init", "--dry-run"])
        assert result.exit_code == 0
        assert "Would create" in result.output
        assert not (tmp_path / "config" / "oc-selector" / "config.toml").exists()

    def test_failure_recover_conflict(self, setup_registry: Path) -> None:  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        result = runner.invoke(app, ["init", "--recover", "--dry-run"])
        assert result.exit_code == 2
        assert "--recover" in result.output


class TestCreateFlags:
    def test_happy_create_dry_run(self, setup_registry: Path) -> None:
        result = runner.invoke(app, ["create", "newprof", "newdir", "--dry-run"])
        assert result.exit_code == 0
        assert "would create" in result.output
        assert "newprof" not in load_registry(setup_registry).profiles

    def test_happy_create_persists_model(self, setup_registry: Path) -> None:
        result = runner.invoke(
            app,
            ["create", "withmodel", "wm-dir", "--model", "openai/gpt"],
        )
        assert result.exit_code == 0
        assert load_registry(setup_registry).profiles["withmodel"].model == "openai/gpt"

    def test_failure_create_no_interactive_no_dir(self, setup_registry: Path) -> None:  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        result = runner.invoke(app, ["create", "noint", "--no-interactive"])
        assert result.exit_code == 2
        assert "Directory is required" in result.output


class TestEditFlags:
    def test_happy_edit_dry_run(self, setup_registry: Path) -> None:
        runner.invoke(app, ["create", "editable", "ed-dir"])
        result = runner.invoke(
            app,
            ["edit", "editable", "--directory", "ed-dir2", "--dry-run"],
        )
        assert result.exit_code == 0
        assert "would edit" in result.output
        assert load_registry(setup_registry).profiles["editable"].directory == "ed-dir"

    def test_happy_edit_persists_model(self, setup_registry: Path) -> None:
        runner.invoke(app, ["create", "editable", "ed-dir"])
        result = runner.invoke(app, ["edit", "editable", "--model", "anthropic/claude"])
        assert result.exit_code == 0
        assert (
            load_registry(setup_registry).profiles["editable"].model
            == "anthropic/claude"
        )


class TestSetDefaultFlags:
    def test_happy_set_default_dry_run(self, setup_registry: Path) -> None:
        runner.invoke(app, ["create", "second", "second-dir"])
        result = runner.invoke(app, ["set-default", "second", "--dry-run"])
        assert result.exit_code == 0
        assert "would set default" in result.output
        assert load_registry(setup_registry).default_profile == "default"


class TestSyncCommand:
    def test_happy_sync_dry_run(self, setup_registry: Path) -> None:  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        prof_dir = resolve_config_parent() / "default"
        prof_dir.mkdir(parents=True, exist_ok=True)
        (prof_dir / "package.json").write_text(
            '{"name": "test-pkg", "version": "1.0.0"}',
        )
        result = runner.invoke(app, ["sync", "default", "--dry-run"])
        assert result.exit_code == 0
        assert "would sync" in result.output

    def test_failure_sync_profile_and_all(self, setup_registry: Path) -> None:  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        result = runner.invoke(app, ["sync", "default", "--all"])
        assert result.exit_code == 2
        assert "both PROFILE and --all" in result.output
