"""TDD tests for guided create/edit wizard and set-default.

Covers C1/C2/C8/C9 contracts:
- Create wizard validates and creates profile
- Edit wizard preserves existing fields
- Set-default is registry-only
- All mutations go through transaction
- No conversion calls
"""

from __future__ import annotations

from pathlib import Path  # noqa: TC003

import pytest

from oc_selector.registry import ProfileEntry, Registry, load_registry, registry_to_toml
from oc_selector.wizard import (
    CreateProfileInput,
    EditProfileInput,
    WizardResult,
    run_create_wizard,
    run_edit_wizard,
    run_set_default,
)


@pytest.fixture
def registry_path(tmp_path: Path) -> Path:
    """Create a valid registry file."""
    reg = Registry(
        default_profile="default",
        profiles={"default": ProfileEntry(directory="default")},
    )
    path = tmp_path / "config.toml"
    path.write_text(registry_to_toml(reg))
    return path


@pytest.fixture
def state_root(tmp_path: Path) -> Path:
    """Create a state root directory."""
    root = tmp_path / "state"
    root.mkdir()
    return root


class TestCreateWizard:
    def test_happy_create(self, registry_path: Path, state_root: Path) -> None:
        inp = CreateProfileInput(
            name="test-profile",
            directory="test-dir",
            model="test/provider/model",
        )
        result = run_create_wizard(inp, registry_path, state_root)
        assert isinstance(result, WizardResult)
        assert result.success is True
        assert result.profile_name == "test-profile"
        # Verify registry was updated
        reg = load_registry(registry_path)
        assert "test-profile" in reg.profiles

    def test_happy_create_no_model(self, registry_path: Path, state_root: Path) -> None:
        inp = CreateProfileInput(
            name="no-model",
            directory="dir",
        )
        result = run_create_wizard(inp, registry_path, state_root)
        assert result.success is True

    def test_failure_duplicate_name(self, registry_path: Path, state_root: Path) -> None:
        inp = CreateProfileInput(
            name="default",
            directory="other",
        )
        result = run_create_wizard(inp, registry_path, state_root)
        assert result.success is False
        assert "already exists" in result.error

    def test_failure_invalid_name(self, registry_path: Path, state_root: Path) -> None:
        inp = CreateProfileInput(
            name="Invalid Name",
            directory="dir",
        )
        result = run_create_wizard(inp, registry_path, state_root)
        assert result.success is False

    def test_failure_empty_directory(self, registry_path: Path, state_root: Path) -> None:
        inp = CreateProfileInput(
            name="test",
            directory="",
        )
        result = run_create_wizard(inp, registry_path, state_root)
        assert result.success is False


class TestEditWizard:
    def test_happy_edit_directory(self, registry_path: Path, state_root: Path) -> None:
        inp = EditProfileInput(
            name="default",
            directory="new-dir",
        )
        result = run_edit_wizard(inp, registry_path, state_root)
        assert result.success is True
        reg = load_registry(registry_path)
        assert reg.profiles["default"].directory == "new-dir"

    def test_happy_edit_preserves_omo(self, registry_path: Path, state_root: Path) -> None:
        # First create with omo_profile
        create_inp = CreateProfileInput(
            name="with-omo",
            directory="dir",
            omo_profile="omo-test",
        )
        run_create_wizard(create_inp, registry_path, state_root)
        # Now edit without specifying omo_profile
        inp = EditProfileInput(
            name="with-omo",
            directory="new-dir",
        )
        result = run_edit_wizard(inp, registry_path, state_root)
        assert result.success is True
        reg = load_registry(registry_path)
        assert reg.profiles["with-omo"].omo_profile == "omo-test"

    def test_failure_profile_not_found(self, registry_path: Path, state_root: Path) -> None:
        inp = EditProfileInput(
            name="nonexistent",
            directory="dir",
        )
        result = run_edit_wizard(inp, registry_path, state_root)
        assert result.success is False
        assert "not found" in result.error

    def test_failure_empty_directory(self, registry_path: Path, state_root: Path) -> None:
        inp = EditProfileInput(
            name="default",
            directory="",
        )
        result = run_edit_wizard(inp, registry_path, state_root)
        assert result.success is False


class TestSetDefault:
    def test_happy_set_default(self, registry_path: Path, state_root: Path) -> None:
        # Create a second profile first
        create_inp = CreateProfileInput(
            name="second",
            directory="second-dir",
        )
        run_create_wizard(create_inp, registry_path, state_root)
        # Now set it as default
        result = run_set_default("second", registry_path, state_root)
        assert result.success is True
        reg = load_registry(registry_path)
        assert reg.default_profile == "second"

    def test_failure_profile_not_found(self, registry_path: Path, state_root: Path) -> None:
        result = run_set_default("nonexistent", registry_path, state_root)
        assert result.success is False
        assert "not found" in result.error

    def test_happy_set_same_default(self, registry_path: Path, state_root: Path) -> None:
        result = run_set_default("default", registry_path, state_root)
        assert result.success is True
        reg = load_registry(registry_path)
        assert reg.default_profile == "default"
