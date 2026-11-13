"""TDD tests for Typer management CLI and raw dispatch.

Covers:
- CLI routes management commands
- Raw dispatch forwards args to native
- Error handling with proper exit codes
- No silent fallbacks
"""

from __future__ import annotations

import json
import os
import runpy
import sys
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING

import pytest
from pydantic import TypeAdapter
from typer.testing import CliRunner

import oc_selector.cli as cli_module
from oc_selector import __version__
from oc_selector.cli import app, main
from oc_selector.omo_upgrade import UpgradeResult
from oc_selector.paths import (
    global_agents_md_path,
    resolve_config_parent,
    resolve_profile_directory,
)
from oc_selector.registry import ProfileEntry, Registry, load_registry, registry_to_toml

if TYPE_CHECKING:
    from collections.abc import Callable

    from helpers.process_harness import ProcessHarness

    from oc_selector.routing import NativeRoute

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


@pytest.fixture
def omo_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Registry with an OMO-eligible profile plus an isolated HOME."""
    config_dir = tmp_path / "xdg-config"
    (config_dir / "oc-selector").mkdir(parents=True)
    reg = Registry(
        default_profile="work",
        profiles={
            "work": ProfileEntry(directory="work-dir", omo_profile="omo-work"),
            "plain": ProfileEntry(directory="plain-dir"),
        },
    )
    reg_path = config_dir / "oc-selector" / "config.toml"
    reg_path.write_text(registry_to_toml(reg))
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_dir))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return reg_path, home


def _fake_isatty(_self: object) -> bool:
    """Report a TTY so the prompt gate opens; stdin content is fed via input."""
    return True


def _setup_home_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Registry with one profile under a home-rooted XDG_CONFIG_HOME.

    The config parent lives under HOME, so the resolved directory abbreviates
    with ``~``. Returns the expected ``~/.config/...`` display form.
    """
    home = tmp_path / "home"
    home.mkdir()
    config_dir = home / ".config"
    (config_dir / "oc-selector").mkdir(parents=True)
    reg = Registry(
        default_profile="default",
        profiles={"default": ProfileEntry(directory="oc-selector/profiles/standard")},
    )
    (config_dir / "oc-selector" / "config.toml").write_text(registry_to_toml(reg))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_dir))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return "~/.config/oc-selector/profiles/standard"


def _profile_named(payload: dict[str, object], name: str) -> dict[str, object]:
    """Return the profile entry with the given name from a list/info JSON payload."""
    profiles = _PROFILES_ADAPTER.validate_python(payload["profiles"])
    for prof in profiles:
        if prof["name"] == name:
            return prof
    msg = f"profile {name!r} not found in payload"
    raise AssertionError(msg)


class TestInitCommand:
    def test_happy_init(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        assert "Created" in result.output or "Already" in result.output

    def test_happy_init_already_initialized(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        first = runner.invoke(app, ["init"])
        assert first.exit_code == 0
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        assert "Already initialized." in result.output

    def test_happy_init_dry_run_already_initialized(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        first = runner.invoke(app, ["init"])
        assert first.exit_code == 0
        result = runner.invoke(app, ["init", "--dry-run"])
        assert result.exit_code == 0
        assert "Nothing to create (already initialized)." in result.output

    def test_failure_init_unwritable_parent(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # XDG_CONFIG_HOME is a file, so the registry directory cannot be made.
        blocker = tmp_path / "blocker"
        blocker.write_text("i am a file")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(blocker))
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 2
        assert "Cannot create registry" in result.output


class TestListCommand:
    def test_happy_list(self, setup_registry: Path) -> None:  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "default" in result.output

    def test_happy_list_shows_resolved_directory(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The list view prints the home-abbreviated resolved directory."""
        expected = _setup_home_profile(tmp_path, monkeypatch)
        result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert f"default -> {expected}" in result.output

    def test_happy_list_json_directory_is_resolved(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The list --json directory is the home-abbreviated resolved form."""
        expected = _setup_home_profile(tmp_path, monkeypatch)
        result = runner.invoke(app, ["list", "--json"])
        assert result.exit_code == 0
        payload = _PAYLOAD_ADAPTER.validate_python(json.loads(result.output))
        prof = _profile_named(payload, "default")
        assert prof["directory"] == expected
        assert "resolved_directory" not in prof

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

    def test_happy_create_prompted_directory(
        self,
        setup_registry: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("typer.testing._NamedTextIOWrapper.isatty", _fake_isatty)
        result = runner.invoke(app, ["create", "prompted"], input="prompted-dir\n")
        assert result.exit_code == 0
        assert (
            load_registry(setup_registry).profiles["prompted"].directory
            == "prompted-dir"
        )

    def test_happy_create_prompted_empty_uses_default(
        self,
        setup_registry: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Empty or whitespace prompt input falls back to the default directory.
        monkeypatch.setattr("typer.testing._NamedTextIOWrapper.isatty", _fake_isatty)
        result = runner.invoke(app, ["create", "prompted"], input="   \n")
        assert result.exit_code == 0
        assert (
            load_registry(setup_registry).profiles["prompted"].directory
            == "oc-selector/profiles/prompted"
        )


class TestEditCommand:
    def test_failure_edit_not_found(self, setup_registry: Path) -> None:  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        result = runner.invoke(app, ["edit", "ghost", "--directory", "ghost-dir"])
        assert result.exit_code == 1
        assert "Profile 'ghost' not found" in result.output


class TestDelete:
    def test_happy_delete(self, setup_registry: Path) -> None:  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        runner.invoke(app, ["create", "second", "second-dir"])
        result = runner.invoke(app, ["delete", "second"])
        assert result.exit_code == 0
        assert "Deleted profile 'second'" in result.output

    def test_failure_delete_not_found(self, setup_registry: Path) -> None:  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        result = runner.invoke(app, ["delete", "ghost"])
        assert result.exit_code == 1
        assert "Profile 'ghost' not found" in result.output

    def test_failure_delete_default(self, setup_registry: Path) -> None:  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        result = runner.invoke(app, ["delete", "default"])
        assert result.exit_code == 1
        assert (
            "Cannot delete default profile 'default'; "
            "run 'oc-selector set-default <other>' first."
        ) in result.output

    def test_happy_delete_dry_run(self, setup_registry: Path) -> None:
        runner.invoke(app, ["create", "second", "second-dir"])
        before = setup_registry.read_bytes()
        result = runner.invoke(app, ["delete", "second", "--dry-run"])
        assert result.exit_code == 0
        assert "Dry run: would delete profile 'second'" in result.output
        assert setup_registry.read_bytes() == before


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

    def test_happy_recover_pending(self, setup_registry: Path) -> None:
        journals = setup_registry.parent / "journals"
        journals.mkdir()
        journal_data: dict[str, object] = {
            "id": "rec-1",
            "operation": "create",
            "phase": "prepared",
            "resources": [],
            "affected_profiles": [],
            "pid": 0,
            "host": "test",
            "timestamp": "2025-01-01T00:00:00Z",
        }
        (journals / "rec-1.json").write_text(json.dumps(journal_data))
        result = runner.invoke(app, ["init", "--recover"])
        assert result.exit_code == 0
        assert "Recovered: rec-1" in result.output

    def test_failure_recover_restore_error(
        self,
        setup_registry: Path,
        tmp_path: Path,
    ) -> None:
        # The resource's parent directory is read-only, so the copy fails.
        journals = setup_registry.parent / "journals"
        journals.mkdir()
        ro_dir = tmp_path / "ro"
        ro_dir.mkdir()
        (ro_dir / "target.bak").write_text("backup")
        ro_dir.chmod(0o500)
        journal_data: dict[str, object] = {
            "id": "rec-fail",
            "operation": "create",
            "phase": "prepared",
            "resources": [
                {
                    "path": str(ro_dir / "target"),
                    "preimage_hash": "abc123",
                    "existed": True,
                },
            ],
            "affected_profiles": [],
            "pid": 0,
            "host": "test",
            "timestamp": "2025-01-01T00:00:00Z",
        }
        (journals / "rec-fail.json").write_text(json.dumps(journal_data))
        try:
            result = runner.invoke(app, ["init", "--recover"])
        finally:
            ro_dir.chmod(0o700)
        assert result.exit_code == 1
        assert "Failed: rec-fail - Cannot restore" in result.output


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

    def test_happy_info_shows_resolved_directory(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The info profile line prints the home-abbreviated resolved directory."""
        expected = _setup_home_profile(tmp_path, monkeypatch)
        result = runner.invoke(app, ["info"])
        assert result.exit_code == 0
        assert f"default: {expected} (" in result.output

    def test_happy_info_json_directory_is_resolved(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The info --json directory is the home-abbreviated resolved form."""
        expected = _setup_home_profile(tmp_path, monkeypatch)
        result = runner.invoke(app, ["info", "--json"])
        assert result.exit_code == 0
        payload = _PAYLOAD_ADAPTER.validate_python(json.loads(result.output))
        prof = _profile_named(payload, "default")
        assert prof["directory"] == expected
        assert "resolved_directory" not in prof

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

    def test_happy_create_no_dir_defaults_to_prefix(self, setup_registry: Path) -> None:
        result = runner.invoke(app, ["create", "noint", "--no-interactive"])
        assert result.exit_code == 0
        prof = load_registry(setup_registry).profiles["noint"]
        assert prof.directory == "oc-selector/profiles/noint"

    def test_happy_create_explicit_dir_overrides_default(
        self,
        setup_registry: Path,
    ) -> None:
        result = runner.invoke(
            app,
            ["create", "custom", "elsewhere", "--no-interactive"],
        )
        assert result.exit_code == 0
        assert load_registry(setup_registry).profiles["custom"].directory == "elsewhere"


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


class TestOmoProfileFlag:
    def test_create_omo_profile_stored(self, setup_registry: Path) -> None:
        result = runner.invoke(
            app,
            ["create", "withomo", "wo-dir", "--omo-profile", "omo-a"],
        )
        assert result.exit_code == 0
        assert load_registry(setup_registry).profiles["withomo"].omo_profile == "omo-a"

    def test_create_without_omo_profile_null(self, setup_registry: Path) -> None:
        result = runner.invoke(app, ["create", "plainprof", "pl-dir"])
        assert result.exit_code == 0
        assert load_registry(setup_registry).profiles["plainprof"].omo_profile is None

    def test_edit_omo_profile_updates(self, setup_registry: Path) -> None:
        runner.invoke(
            app,
            ["create", "editable", "ed-dir", "--omo-profile", "omo-old"],
        )
        result = runner.invoke(app, ["edit", "editable", "--omo-profile", "omo-new"])
        assert result.exit_code == 0
        assert (
            load_registry(setup_registry).profiles["editable"].omo_profile == "omo-new"
        )

    def test_edit_without_omo_profile_preserves(self, setup_registry: Path) -> None:
        runner.invoke(
            app,
            ["create", "editable", "ed-dir", "--omo-profile", "omo-keep"],
        )
        result = runner.invoke(app, ["edit", "editable", "--directory", "ed-dir2"])
        assert result.exit_code == 0
        reg = load_registry(setup_registry)
        assert reg.profiles["editable"].omo_profile == "omo-keep"
        assert reg.profiles["editable"].directory == "ed-dir2"

    def test_create_omo_profile_dry_run_no_write(self, setup_registry: Path) -> None:
        before = setup_registry.read_bytes()
        result = runner.invoke(
            app,
            ["create", "dryprof", "dr-dir", "--omo-profile", "omo-x", "--dry-run"],
        )
        assert result.exit_code == 0
        assert "would create" in result.output
        assert setup_registry.read_bytes() == before

    def test_list_json_shows_omo_profile(self, setup_registry: Path) -> None:  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        runner.invoke(
            app,
            ["create", "withomo", "wo-dir", "--omo-profile", "omo-list"],
        )
        result = runner.invoke(app, ["list", "--json"])
        assert result.exit_code == 0
        payload = _PAYLOAD_ADAPTER.validate_python(json.loads(result.output))
        profiles = _PROFILES_ADAPTER.validate_python(payload["profiles"])
        entry = next(p for p in profiles if p["name"] == "withomo")
        assert entry["omo_profile"] == "omo-list"


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

    def test_happy_sync_all_dry_run(self, setup_registry: Path) -> None:  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        prof_dir = resolve_config_parent() / "default"
        prof_dir.mkdir(parents=True, exist_ok=True)
        (prof_dir / "package.json").write_text(
            '{"name": "test-pkg", "version": "1.0.0"}',
        )
        result = runner.invoke(app, ["sync", "--all", "--dry-run"])
        assert result.exit_code == 0
        assert "default: Dry run: would sync test-pkg to latest" in result.output

    def test_failure_sync_profile_not_found(self, setup_registry: Path) -> None:  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        result = runner.invoke(app, ["sync", "ghost"])
        assert result.exit_code == 2
        assert "Profile 'ghost' not found" in result.output

    def test_failure_sync_no_package(self, setup_registry: Path) -> None:  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        (resolve_config_parent() / "default").mkdir(parents=True, exist_ok=True)
        result = runner.invoke(app, ["sync", "default"])
        assert result.exit_code == 1
        error = "No package.json or pyproject.toml found"
        msg = f"default: No host package detected ({error})"
        assert msg in result.output


@pytest.fixture
def link_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Registry with two profiles plus isolated XDG paths.

    Returns ``(registry_path, config_parent)``. The global guard is simulated by
    the test writing ``<config_parent>/opencode/AGENTS.md``.
    """
    config_dir = tmp_path / "xdg-config"
    (config_dir / "oc-selector").mkdir(parents=True)
    reg = Registry(
        default_profile="work",
        profiles={
            "work": ProfileEntry(directory="work-dir"),
            "personal": ProfileEntry(directory="personal-dir"),
        },
    )
    reg_path = config_dir / "oc-selector" / "config.toml"
    reg_path.write_text(registry_to_toml(reg))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_dir))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return reg_path, config_dir


def _write_global_guard(config_dir: Path) -> Path:
    global_path = config_dir / "opencode" / "AGENTS.md"
    global_path.parent.mkdir(parents=True, exist_ok=True)
    global_path.write_text("# global guard\n")
    return global_path


def _profile_link(config_parent: Path, directory: str) -> Path:
    return resolve_profile_directory(config_parent, directory) / "AGENTS.md"


class TestLinkCommand:
    def test_happy_link_all_creates_symlinks(self, link_env: tuple[Path, Path]) -> None:
        _, config_dir = link_env
        _write_global_guard(config_dir)
        result = runner.invoke(app, ["link", "--all"])
        assert result.exit_code == 0
        expected = str(global_agents_md_path().resolve())
        for name in ("work", "personal"):
            link = _profile_link(resolve_config_parent(), f"{name}-dir")
            assert link.is_symlink()
            assert str(link.readlink()) == expected

    def test_link_single_profile(self, link_env: tuple[Path, Path]) -> None:
        _, config_dir = link_env
        _write_global_guard(config_dir)
        result = runner.invoke(app, ["link", "personal"])
        assert result.exit_code == 0
        assert "personal: linked" in result.output
        assert not _profile_link(resolve_config_parent(), "work-dir").is_symlink()

    def test_link_missing_profile_exits_2(self, link_env: tuple[Path, Path]) -> None:
        _, config_dir = link_env
        _write_global_guard(config_dir)
        result = runner.invoke(app, ["link", "ghost"])
        assert result.exit_code == 2
        assert "Profile 'ghost' not found" in result.output

    def test_link_dry_run_changes_nothing(self, link_env: tuple[Path, Path]) -> None:
        reg_path, config_dir = link_env
        _write_global_guard(config_dir)
        result = runner.invoke(app, ["link", "--all", "--dry-run"])
        assert result.exit_code == 0
        assert "would link" in result.output
        for name in ("work", "personal"):
            link = _profile_link(resolve_config_parent(), f"{name}-dir")
            assert not link.is_symlink()
        assert load_registry(reg_path).default_profile == "work"

    def test_link_all_idempotent(self, link_env: tuple[Path, Path]) -> None:
        _, config_dir = link_env
        _write_global_guard(config_dir)
        work_link = _profile_link(resolve_config_parent(), "work-dir")
        assert runner.invoke(app, ["link", "--all"]).exit_code == 0
        first_target = str(work_link.readlink())
        second = runner.invoke(app, ["link", "--all"])
        assert second.exit_code == 0
        assert str(work_link.readlink()) == first_target

    def test_link_profile_and_all_rejected(self, link_env: tuple[Path, Path]) -> None:
        _, config_dir = link_env
        _write_global_guard(config_dir)
        result = runner.invoke(app, ["link", "work", "--all"])
        assert result.exit_code == 2
        assert "both PROFILE and --all" in result.output

    def test_link_symlink_failure_warns_and_continues(
        self,
        link_env: tuple[Path, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _, config_dir = link_env
        _write_global_guard(config_dir)
        real_symlink = os.symlink

        def _selective_raise(
            src: str,
            dst: str,
            target_is_directory: bool = False,  # noqa: FBT001, FBT002  # mirrors os.symlink
        ) -> None:
            # Fail only the "work" profile's link; "personal" still links.
            if "work-dir" in str(dst):
                raise PermissionError("denied")
            real_symlink(src, dst, target_is_directory=target_is_directory)

        monkeypatch.setattr("os.symlink", _selective_raise)
        result = runner.invoke(app, ["link", "--all"])
        assert result.exit_code == 0
        assert "work: warning" in result.output
        assert _profile_link(resolve_config_parent(), "personal-dir").is_symlink()
        assert not _profile_link(resolve_config_parent(), "work-dir").is_symlink()


class TestUpgradeOmo:
    def test_failure_profile_and_all(self, omo_env: tuple[Path, Path]) -> None:  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        result = runner.invoke(app, ["upgrade-omo", "work", "--all"])
        assert result.exit_code == 2
        assert "cannot specify both PROFILE and --all" in result.output

    def test_happy_no_eligible(self, omo_env: tuple[Path, Path]) -> None:
        reg_path, _ = omo_env
        reg = Registry(
            default_profile="plain",
            profiles={"plain": ProfileEntry(directory="plain-dir")},
        )
        reg_path.write_text(registry_to_toml(reg))
        result = runner.invoke(app, ["upgrade-omo", "--all"])
        assert result.exit_code == 0
        assert "No eligible profiles (none declare an OMO profile)." in result.output

    def test_failure_profile_not_found(self, omo_env: tuple[Path, Path]) -> None:  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        result = runner.invoke(app, ["upgrade-omo", "ghost"])
        assert result.exit_code == 2
        assert "Profile 'ghost' not found" in result.output

    def test_failure_profile_without_omo(self, omo_env: tuple[Path, Path]) -> None:  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        result = runner.invoke(app, ["upgrade-omo", "plain"])
        assert result.exit_code == 2
        assert (
            "Profile 'plain' does not declare an OMO profile; nothing to upgrade"
            in result.output
        )

    def test_failure_omo_config_missing(self, omo_env: tuple[Path, Path]) -> None:  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        result = runner.invoke(app, ["upgrade-omo", "work", "--version", "1.2.3"])
        assert result.exit_code == 2
        assert "OMO config not found" in result.output

    def test_failure_omo_config_invalid_json(self, omo_env: tuple[Path, Path]) -> None:
        _, home = omo_env
        (home / ".omo").write_text("{not json")
        result = runner.invoke(app, ["upgrade-omo", "work", "--version", "1.2.3"])
        assert result.exit_code == 2
        assert "Invalid OMO config" in result.output

    def test_failure_omo_config_not_object(self, omo_env: tuple[Path, Path]) -> None:
        _, home = omo_env
        (home / ".omo").write_text("[1, 2]")
        result = runner.invoke(app, ["upgrade-omo", "work", "--version", "1.2.3"])
        assert result.exit_code == 2
        assert "expected a JSON object." in result.output

    def test_failure_host_version_missing(self, omo_env: tuple[Path, Path]) -> None:
        reg_path, home = omo_env
        (home / ".omo").write_text('{"version": "1.0.0"}')
        reg = Registry(
            default_profile="work",
            opencode_executable="ocx-missing",
            profiles={
                "work": ProfileEntry(directory="work-dir", omo_profile="omo-work"),
            },
        )
        reg_path.write_text(registry_to_toml(reg))
        result = runner.invoke(app, ["upgrade-omo", "work"])
        assert result.exit_code == 2
        assert (
            "Cannot resolve host version from 'ocx-missing' (status: missing)."
            in result.output
        )

    def test_failure_host_version_unsupported(
        self,
        omo_env: tuple[Path, Path],
        monkeypatch: pytest.MonkeyPatch,
        process_harness: ProcessHarness,
        fixture_executable: Callable[..., Path],
    ) -> None:
        _, home = omo_env
        (home / ".omo").write_text('{"version": "1.0.0"}')
        fixture_executable("opencode", stdout="2.0.0\n")
        monkeypatch.setenv("PATH", str(process_harness.bin_dir))
        result = runner.invoke(app, ["upgrade-omo", "work"])
        assert result.exit_code == 2
        assert "Unsupported host version for maintenance: '2.0.0'" in result.output

    def test_happy_explicit_version_dry_run(self, omo_env: tuple[Path, Path]) -> None:
        _, home = omo_env
        (home / ".omo").write_text('{"version": "1.0.0"}')
        result = runner.invoke(
            app,
            ["upgrade-omo", "work", "--version", "1.2.3", "--dry-run"],
        )
        assert result.exit_code == 0
        assert "Dry run: would upgrade from 1.0.0 to 1.2.3" in result.output
        assert json.loads((home / ".omo").read_text())["version"] == "1.0.0"

    def test_happy_host_resolved_version(
        self,
        omo_env: tuple[Path, Path],
        monkeypatch: pytest.MonkeyPatch,
        process_harness: ProcessHarness,
        fixture_executable: Callable[..., Path],
    ) -> None:
        _, home = omo_env
        (home / ".omo").write_text('{"version": "1.0.0"}')
        fixture_executable("opencode", stdout="1.2.3\n")
        monkeypatch.setenv("PATH", str(process_harness.bin_dir))
        result = runner.invoke(app, ["upgrade-omo", "work"])
        assert result.exit_code == 0
        assert "Upgraded from 1.0.0 to 1.2.3" in result.output
        assert json.loads((home / ".omo").read_text())["version"] == "1.2.3"

    def test_happy_all_eligible(self, omo_env: tuple[Path, Path]) -> None:
        _, home = omo_env
        (home / ".omo").write_text('{"version": "1.0.0"}')
        result = runner.invoke(
            app,
            ["upgrade-omo", "--all", "--version", "1.2.3", "--dry-run"],
        )
        assert result.exit_code == 0
        assert "Dry run: would upgrade from 1.0.0 to 1.2.3" in result.output

    def test_failure_upgrade_error(
        self,
        omo_env: tuple[Path, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _, home = omo_env
        (home / ".omo").write_text('{"version": "1.0.0"}')

        def fake_upgrade(*_args: object, **_kwargs: object) -> UpgradeResult:
            return UpgradeResult(
                success=False,
                from_version="1.0.0",
                to_version="1.2.3",
                message="Upgrade failed",
                error="simulated lock timeout",
            )

        monkeypatch.setattr("oc_selector.cli.run_omo_upgrade", fake_upgrade)
        result = runner.invoke(app, ["upgrade-omo", "work", "--version", "1.2.3"])
        assert result.exit_code == 1
        assert "simulated lock timeout" in result.output


class TestMainDispatch:
    def test_happy_management_route(
        self,
        setup_registry: Path,  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["oc-selector", "list"])
        with pytest.raises(SystemExit) as excinfo:
            main()
        assert excinfo.value.code == 0

    def test_happy_module_main_entry(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Running the module as __main__ calls main() and exits 2 with no registry."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "nonexistent"))
        monkeypatch.setattr(sys, "argv", ["oc-selector", "list"])
        with pytest.raises(SystemExit) as excinfo:
            runpy.run_path(cli_module.__file__, run_name="__main__")
        assert excinfo.value.code == 2
        captured = capsys.readouterr()
        assert "No registry found" in captured.err

    def test_failure_raw_no_registry(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # The raw/native path now exits cleanly with the intended code (2) and no
        # traceback: main() converts the native-branch typer.Exit to SystemExit.
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "nonexistent"))
        monkeypatch.setattr(sys, "argv", ["oc-selector", "run"])
        with pytest.raises(SystemExit) as excinfo:
            main()
        assert excinfo.value.code == 2
        captured = capsys.readouterr()
        assert "No registry found" in captured.err
        assert "Traceback" not in captured.err

    def test_happy_raw_dispatch(
        self,
        setup_registry: Path,  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        monkeypatch: pytest.MonkeyPatch,
        process_harness: ProcessHarness,
        fixture_executable: Callable[..., Path],
    ) -> None:
        fixture_executable("opencode", stdout="ok\n")
        monkeypatch.setenv("PATH", str(process_harness.bin_dir))
        routes: list[NativeRoute] = []

        def fake_launch(
            route: NativeRoute,
            _registry: Registry,
            *,
            _dry_run: bool = False,
        ) -> int:
            routes.append(route)
            return 0

        monkeypatch.setattr("oc_selector.cli.launch_native", fake_launch)
        monkeypatch.setattr(sys, "argv", ["oc-selector", "--model", "openai/gpt"])
        main()
        assert len(routes) == 1
        assert routes[0].argv == ("--model", "openai/gpt")

    def test_failure_raw_executable_missing(
        self,
        setup_registry: Path,  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("PATH", str(tmp_path / "empty"))
        monkeypatch.setattr(sys, "argv", ["oc-selector", "run"])
        with pytest.raises(SystemExit) as excinfo:
            main()
        # A missing executable is honored as its class's code (127), cleanly.
        assert excinfo.value.code == 127

    def test_failure_raw_executable_non_executable(
        self,
        setup_registry: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # A present-but-non-executable executable (given as an absolute path) is
        # honored as 126. Skipped as root, where os.access(X_OK) is always true.
        if getattr(os, "geteuid", lambda: -1)() == 0:
            pytest.skip("os.access(X_OK) is meaningless for root")
        bad = tmp_path / "opencode"
        bad.write_text("#!/bin/sh\n")
        bad.chmod(0o644)
        reg_path = setup_registry.parent / "config.toml"
        lines = [
            "schema_version = 1",
            'default_profile = "default"',
            f'opencode_executable = "{bad}"',
            "[profiles.default]",
            'directory = "."',
        ]
        reg_path.write_text("\n".join(lines) + "\n")
        monkeypatch.setattr(sys, "argv", ["oc-selector", "run"])
        with pytest.raises(SystemExit) as excinfo:
            main()
        assert excinfo.value.code == 126

    def test_completion_bad_shell_exit_2_no_traceback(
        self,
        setup_registry: Path,  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # An unsupported shell is a usage error: exit 2, no traceback.
        monkeypatch.setattr(sys, "argv", ["oc-selector", "completion", "zzz"])
        with pytest.raises(SystemExit) as excinfo:
            main()
        assert excinfo.value.code == 2
        captured = capsys.readouterr()
        assert "Unsupported shell" in captured.err
        assert "Traceback" not in captured.err
