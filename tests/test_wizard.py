"""TDD tests for guided create/edit wizard and set-default.

Covers:
- Create wizard validates and creates profile
- Create links the profile's AGENTS.md to the global guard (T4)
- Edit wizard preserves existing fields
- Set-default is registry-only
- All mutations go through transaction
- No conversion calls
"""

from __future__ import annotations

from pathlib import Path  # noqa: TC003

import pytest

from oc_selector.agents_link import link_path
from oc_selector.paths import (
    global_agents_md_path,
    resolve_config_parent,
    resolve_profile_directory,
)
from oc_selector.registry import ProfileEntry, Registry, load_registry, registry_to_toml
from oc_selector.transactions import Transaction
from oc_selector.wizard import (
    CreateProfileInput,
    DeleteProfileInput,
    EditProfileInput,
    WizardResult,
    run_create_wizard,
    run_delete,
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


@pytest.fixture
def block_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``Transaction.commit`` raise ``OSError`` to simulate a failed apply."""

    def _raise(
        self: Transaction,  # noqa: ARG001  # pyright: ignore[reportUnusedParameter]
    ) -> None:
        raise OSError("simulated commit failure")

    monkeypatch.setattr(Transaction, "commit", _raise)


def _global_guard(isolated_home: Path) -> Path:
    """Create the global ``AGENTS.md`` guard and return its absolute path."""
    target = isolated_home / ".config" / "opencode" / "AGENTS.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("global guard\n")
    return target


class TestCreateWizard:
    def test_happy_create(
        self,
        isolated_home: Path,  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        registry_path: Path,
        state_root: Path,
    ) -> None:
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

    def test_happy_create_no_model(
        self,
        isolated_home: Path,  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        registry_path: Path,
        state_root: Path,
    ) -> None:
        inp = CreateProfileInput(
            name="no-model",
            directory="dir",
        )
        result = run_create_wizard(inp, registry_path, state_root)
        assert result.success is True

    def test_failure_duplicate_name(
        self,
        isolated_home: Path,  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        registry_path: Path,
        state_root: Path,
    ) -> None:
        inp = CreateProfileInput(
            name="default",
            directory="other",
        )
        result = run_create_wizard(inp, registry_path, state_root)
        assert result.success is False
        assert result.error is not None
        assert "already exists" in result.error

    def test_failure_invalid_name(
        self,
        isolated_home: Path,  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        registry_path: Path,
        state_root: Path,
    ) -> None:
        inp = CreateProfileInput(
            name="Invalid Name",
            directory="dir",
        )
        result = run_create_wizard(inp, registry_path, state_root)
        assert result.success is False

    def test_failure_empty_directory(
        self,
        isolated_home: Path,  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        registry_path: Path,
        state_root: Path,
    ) -> None:
        inp = CreateProfileInput(
            name="test",
            directory="",
        )
        result = run_create_wizard(inp, registry_path, state_root)
        assert result.success is False
        assert result.exit_code == 2

    def test_failure_cannot_load_registry_invalid_toml(
        self,
        isolated_home: Path,  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        tmp_path: Path,
        state_root: Path,
    ) -> None:
        """Create wizard fails with 'Cannot load registry' on unparseable TOML."""
        registry_path = tmp_path / "config.toml"
        registry_path.write_text("this is not toml [[[")
        inp = CreateProfileInput(
            name="new-profile",
            directory="dir",
        )
        result = run_create_wizard(inp, registry_path, state_root)
        assert result.success is False
        assert result.message == "Cannot load registry"
        assert result.error is not None

    def test_failure_transaction_commit_error(
        self,
        isolated_home: Path,  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        registry_path: Path,
        state_root: Path,
        block_commit: None,  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
    ) -> None:
        """Commit failure yields 'Transaction failed' and leaves registry intact."""
        original = registry_path.read_text()
        inp = CreateProfileInput(
            name="new-profile",
            directory="dir",
        )
        result = run_create_wizard(inp, registry_path, state_root)
        assert result.success is False
        assert result.message == "Transaction failed"
        assert result.error is not None
        assert registry_path.read_text() == original
        reg = load_registry(registry_path)
        assert "new-profile" not in reg.profiles


class TestCreateWizardAgentsLink:
    """T4: create links the profile's AGENTS.md to the global guard."""

    def test_links_when_global_present(
        self,
        isolated_home: Path,
        registry_path: Path,
        state_root: Path,
    ) -> None:
        target = _global_guard(isolated_home)
        inp = CreateProfileInput(name="linked", directory="work")
        result = run_create_wizard(inp, registry_path, state_root)
        assert result.success is True
        profile_dir = resolve_profile_directory(
            resolve_config_parent(),
            inp.directory,
        )
        link = link_path(profile_dir)
        assert link.is_symlink() is True
        assert str(link.readlink()) == str(target.resolve())
        assert profile_dir.is_dir()
        assert result.agents_link is not None
        assert result.agents_link == str(target)

    def test_config_dir_created_on_create(
        self,
        isolated_home: Path,
        registry_path: Path,
        state_root: Path,
    ) -> None:
        _global_guard(isolated_home)
        inp = CreateProfileInput(name="dirs", directory="work")
        result = run_create_wizard(inp, registry_path, state_root)
        assert result.success is True
        profile_dir = resolve_profile_directory(
            resolve_config_parent(),
            inp.directory,
        )
        assert profile_dir.is_dir()

    def test_dangling_when_global_absent(
        self,
        isolated_home: Path,  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        registry_path: Path,
        state_root: Path,
    ) -> None:
        inp = CreateProfileInput(name="dangling", directory="work")
        result = run_create_wizard(inp, registry_path, state_root)
        assert result.success is True
        assert result.agents_link is not None
        assert "dangling" in result.agents_link
        profile_dir = resolve_profile_directory(
            resolve_config_parent(),
            inp.directory,
        )
        link = link_path(profile_dir)
        assert link.is_symlink() is True
        assert str(link.readlink()) == str(global_agents_md_path().resolve())

    def test_dry_run_creates_no_link(
        self,
        isolated_home: Path,
        registry_path: Path,
        state_root: Path,
    ) -> None:
        _global_guard(isolated_home)
        before = registry_path.read_bytes()
        inp = CreateProfileInput(name="dry", directory="work")
        result = run_create_wizard(inp, registry_path, state_root, dry_run=True)
        assert result.success is True
        assert result.agents_link is None
        profile_dir = resolve_profile_directory(
            resolve_config_parent(),
            inp.directory,
        )
        assert not profile_dir.exists()
        assert not link_path(profile_dir).exists()
        assert registry_path.read_bytes() == before

    def test_symlink_failure_warns_and_continues(
        self,
        isolated_home: Path,
        monkeypatch: pytest.MonkeyPatch,
        registry_path: Path,
        state_root: Path,
    ) -> None:
        _global_guard(isolated_home)

        def _raise(*_args: object, **_kwargs: object) -> None:
            raise PermissionError("simulated permission denied")

        monkeypatch.setattr("os.symlink", _raise)
        inp = CreateProfileInput(name="warn", directory="work")
        result = run_create_wizard(inp, registry_path, state_root)
        assert result.success is True
        assert result.agents_link is not None
        assert "warning" in result.agents_link
        profile_dir = resolve_profile_directory(
            resolve_config_parent(),
            inp.directory,
        )
        # No partial link remains (the empty config dir may remain).
        assert not link_path(profile_dir).is_symlink()


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

    def test_happy_edit_preserves_omo(
        self,
        isolated_home: Path,  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        registry_path: Path,
        state_root: Path,
    ) -> None:
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

    def test_failure_profile_not_found(
        self,
        registry_path: Path,
        state_root: Path,
    ) -> None:
        inp = EditProfileInput(
            name="nonexistent",
            directory="dir",
        )
        result = run_edit_wizard(inp, registry_path, state_root)
        assert result.success is False
        assert result.error is not None
        assert "not found" in result.error

    def test_failure_empty_directory(
        self,
        registry_path: Path,
        state_root: Path,
    ) -> None:
        inp = EditProfileInput(
            name="default",
            directory="",
        )
        result = run_edit_wizard(inp, registry_path, state_root)
        assert result.success is False
        assert result.exit_code == 2

    def test_failure_cannot_load_registry_bad_default(
        self,
        tmp_path: Path,
        state_root: Path,
    ) -> None:
        """Edit wizard fails with 'Cannot load registry' when default is missing."""
        registry_path = tmp_path / "config.toml"
        registry_path.write_text(
            """\
schema_version = 1
default_profile = "missing"

[profiles.default]
directory = "default"
""",
        )
        inp = EditProfileInput(
            name="default",
            directory="new-dir",
        )
        result = run_edit_wizard(inp, registry_path, state_root)
        assert result.success is False
        assert result.message == "Cannot load registry"
        assert result.error is not None

    def test_failure_transaction_commit_error(
        self,
        registry_path: Path,
        state_root: Path,
        block_commit: None,  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
    ) -> None:
        """Commit failure yields 'Transaction failed' and leaves registry intact."""
        original = registry_path.read_text()
        inp = EditProfileInput(
            name="default",
            directory="new-dir",
        )
        result = run_edit_wizard(inp, registry_path, state_root)
        assert result.success is False
        assert result.message == "Transaction failed"
        assert result.error is not None
        assert registry_path.read_text() == original
        reg = load_registry(registry_path)
        assert reg.profiles["default"].directory == "default"


class TestEditWizardAgentsLink:
    """T6: edit --directory relinks the profile's AGENTS.md to the global guard."""

    def test_relink_on_directory_change(
        self,
        isolated_home: Path,
        registry_path: Path,
        state_root: Path,
    ) -> None:
        """Directory change: old link gone, new link at new dir pointing at global."""
        target = _global_guard(isolated_home)
        # Create at the original directory (establishes the old link).
        run_create_wizard(
            CreateProfileInput(name="linked", directory="work"),
            registry_path,
            state_root,
        )
        old_dir = resolve_profile_directory(resolve_config_parent(), "work")
        old_link = link_path(old_dir)
        assert old_link.is_symlink()

        inp = EditProfileInput(name="linked", directory="new-dir")
        result = run_edit_wizard(inp, registry_path, state_root)
        assert result.success is True
        reg = load_registry(registry_path)
        assert reg.profiles["linked"].directory == "new-dir"
        assert not old_link.is_symlink()
        new_dir = resolve_profile_directory(resolve_config_parent(), "new-dir")
        new_link = link_path(new_dir)
        assert new_link.is_symlink()
        assert str(new_link.readlink()) == str(target.resolve())
        assert result.agents_link is not None
        assert result.agents_link == str(target)

    def test_no_relink_without_directory_change(
        self,
        isolated_home: Path,
        registry_path: Path,
        state_root: Path,
    ) -> None:
        """Edit without a directory change: link untouched, agents_link None."""
        _global_guard(isolated_home)
        run_create_wizard(
            CreateProfileInput(name="linked", directory="work"),
            registry_path,
            state_root,
        )
        old_dir = resolve_profile_directory(resolve_config_parent(), "work")
        old_link = link_path(old_dir)
        assert old_link.is_symlink()
        before_target = str(old_link.readlink())

        inp = EditProfileInput(name="linked", model="test/provider/model")
        result = run_edit_wizard(inp, registry_path, state_root)
        assert result.success is True
        assert result.agents_link is None
        assert old_link.is_symlink()
        assert str(old_link.readlink()) == before_target

    def test_dry_run_no_relink(
        self,
        isolated_home: Path,
        registry_path: Path,
        state_root: Path,
    ) -> None:
        """Dry-run edit with a directory change makes no link changes."""
        _global_guard(isolated_home)
        run_create_wizard(
            CreateProfileInput(name="linked", directory="work"),
            registry_path,
            state_root,
        )
        old_dir = resolve_profile_directory(resolve_config_parent(), "work")
        old_link = link_path(old_dir)
        assert old_link.is_symlink()
        before = registry_path.read_bytes()

        inp = EditProfileInput(name="linked", directory="new-dir")
        result = run_edit_wizard(inp, registry_path, state_root, dry_run=True)
        assert result.success is True
        assert result.agents_link is None
        assert old_link.is_symlink()
        new_dir = resolve_profile_directory(resolve_config_parent(), "new-dir")
        assert not link_path(new_dir).exists()
        assert registry_path.read_bytes() == before

    def test_symlink_failure_keeps_old_link(
        self,
        isolated_home: Path,
        monkeypatch: pytest.MonkeyPatch,
        registry_path: Path,
        state_root: Path,
    ) -> None:
        """Symlink failure on relink: success=True, warning, OLD link intact."""
        _global_guard(isolated_home)
        run_create_wizard(
            CreateProfileInput(name="linked", directory="work"),
            registry_path,
            state_root,
        )
        old_dir = resolve_profile_directory(resolve_config_parent(), "work")
        old_link = link_path(old_dir)
        assert old_link.is_symlink()

        def _raise(*_args: object, **_kwargs: object) -> None:
            raise PermissionError("simulated permission denied")

        monkeypatch.setattr("os.symlink", _raise)
        inp = EditProfileInput(name="linked", directory="new-dir")
        result = run_edit_wizard(inp, registry_path, state_root)
        assert result.success is True
        assert result.agents_link is not None
        assert "warning" in result.agents_link
        assert old_link.is_symlink()
        new_dir = resolve_profile_directory(resolve_config_parent(), "new-dir")
        assert not link_path(new_dir).is_symlink()


class TestSetDefault:
    def test_happy_set_default(
        self,
        isolated_home: Path,  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        registry_path: Path,
        state_root: Path,
    ) -> None:
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

    def test_failure_profile_not_found(
        self,
        registry_path: Path,
        state_root: Path,
    ) -> None:
        result = run_set_default("nonexistent", registry_path, state_root)
        assert result.success is False
        assert result.error is not None
        assert "not found" in result.error

    def test_happy_set_same_default(
        self,
        registry_path: Path,
        state_root: Path,
    ) -> None:
        result = run_set_default("default", registry_path, state_root)
        assert result.success is True
        reg = load_registry(registry_path)
        assert reg.default_profile == "default"

    def test_failure_cannot_load_registry_invalid_toml(
        self,
        tmp_path: Path,
        state_root: Path,
    ) -> None:
        """Set-default fails with 'Cannot load registry' on unparseable TOML."""
        registry_path = tmp_path / "config.toml"
        registry_path.write_text("not toml at all {{{")
        result = run_set_default("default", registry_path, state_root)
        assert result.success is False
        assert result.message == "Cannot load registry"
        assert result.error is not None

    def test_failure_transaction_commit_error(
        self,
        tmp_path: Path,
        state_root: Path,
        block_commit: None,  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
    ) -> None:
        """Commit failure yields 'Transaction failed' and leaves registry intact."""
        reg = Registry(
            default_profile="default",
            profiles={
                "default": ProfileEntry(directory="default"),
                "second": ProfileEntry(directory="second-dir"),
            },
        )
        registry_path = tmp_path / "config.toml"
        registry_path.write_text(registry_to_toml(reg))
        original = registry_path.read_text()
        result = run_set_default("second", registry_path, state_root)
        assert result.success is False
        assert result.message == "Transaction failed"
        assert result.error is not None
        assert registry_path.read_text() == original
        loaded = load_registry(registry_path)
        assert loaded.default_profile == "default"


class TestDelete:
    def test_happy_delete(
        self,
        isolated_home: Path,  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        registry_path: Path,
        state_root: Path,
    ) -> None:
        run_create_wizard(
            CreateProfileInput(name="second", directory="second-dir"),
            registry_path,
            state_root,
        )
        result = run_delete(
            DeleteProfileInput(name="second"),
            registry_path,
            state_root,
        )
        assert result.success is True
        assert result.message == "Deleted profile 'second'"

    def test_failure_delete_not_found(
        self,
        registry_path: Path,
        state_root: Path,
    ) -> None:
        result = run_delete(DeleteProfileInput(name="ghost"), registry_path, state_root)
        assert result.success is False
        assert result.error == "Profile 'ghost' not found"

    def test_failure_delete_default(
        self,
        registry_path: Path,
        state_root: Path,
    ) -> None:
        result = run_delete(
            DeleteProfileInput(name="default"),
            registry_path,
            state_root,
        )
        assert result.success is False
        expected = (
            "Cannot delete default profile 'default'; "
            "run 'oc-selector set-default <other>' first."
        )
        assert result.error == expected

    def test_happy_delete_dry_run(
        self,
        isolated_home: Path,  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        registry_path: Path,
        state_root: Path,
    ) -> None:
        run_create_wizard(
            CreateProfileInput(name="second", directory="second-dir"),
            registry_path,
            state_root,
        )
        before = registry_path.read_bytes()
        result = run_delete(
            DeleteProfileInput(name="second"),
            registry_path,
            state_root,
            dry_run=True,
        )
        assert result.success is True
        assert result.message == "Dry run: would delete profile 'second'"
        assert registry_path.read_bytes() == before

    def test_failure_delete_last_remaining(
        self,
        registry_path: Path,
        state_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Reach the defensive last-remaining branch via an injected registry."""
        bad = Registry.model_construct(
            schema_version=1,
            default_profile="other",
            opencode_executable="opencode",
            profiles={"only": ProfileEntry(directory="d")},
        )

        def _fake_load(_p: Path) -> Registry:
            return bad

        monkeypatch.setattr("oc_selector.wizard.load_registry", _fake_load)
        result = run_delete(DeleteProfileInput(name="only"), registry_path, state_root)
        assert result.success is False
        assert result.error == "Cannot delete the last remaining profile 'only'."
