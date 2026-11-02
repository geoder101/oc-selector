"""TDD tests for typed registry, portable paths, and routing decisions.

Covers:
- Registry parse/validate/serialize
- Profile name validation (reserved, casefold, device names)
- Path resolution (XDG, home, state root)
- Routing decisions (native, management, profile, --, unknown)
- Management grammar validation
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from oc_selector.errors import ArgumentError, RegistryError
from oc_selector.paths import (
    omo_config_path,
    registry_path,
    resolve_config_parent,
    state_root,
)
from oc_selector.registry import (
    MANAGEMENT_NAMES,
    NATIVE_NAMES,
    ProfileEntry,
    Registry,
    load_registry,
    parse_registry,
    registry_to_toml,
    validate_profile_name,
)
from oc_selector.routing import (
    ManagementRoute,
    NativeRoute,
    is_reserved_name,
    route,
    validate_management_args,
)

FIXTURES = Path(__file__).parent / "fixtures" / "registry"


# --- Path resolution tests ---


class TestPaths:
    def test_happy_xdg_config_parent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/test-xdg")
        assert resolve_config_parent() == Path("/tmp/test-xdg")

    def test_happy_default_parent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        parent = resolve_config_parent()
        assert parent == Path.home() / ".config"

    def test_failure_relative_xdg_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", "relative/path")
        with pytest.raises(RegistryError, match="relative"):
            resolve_config_parent()

    def test_failure_empty_xdg_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", "")
        with pytest.raises(RegistryError, match="empty"):
            resolve_config_parent()

    def test_happy_state_root(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/test-xdg")
        assert state_root() == Path("/tmp/test-xdg/oc-selector")

    def test_happy_registry_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/test-xdg")
        assert registry_path() == Path("/tmp/test-xdg/oc-selector/config.toml")

    def test_happy_omo_path_uses_home(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/test-xdg")
        # OMO always uses actual home, not XDG
        assert omo_config_path() == Path.home() / ".omo"


# --- Registry parse/validate tests ---


class TestRegistryParse:
    def test_happy_valid_registry(self) -> None:
        data = (FIXTURES / "valid.toml").read_text()
        reg = parse_registry(data)
        assert reg.schema_version == 1
        assert reg.default_profile == "default"
        assert reg.opencode_executable == "opencode"
        assert "default" in reg.profiles
        assert reg.profiles["default"].directory == "opencode"

    def test_happy_customized_registry(self) -> None:
        data = (FIXTURES / "customized.toml").read_text()
        reg = parse_registry(data)
        assert reg.default_profile == "gpt"
        assert reg.opencode_executable == "/usr/local/bin/opencode"
        assert len(reg.profiles) == 3
        assert reg.profiles["gpt"].omo_profile == "oc-selector-gpt"
        assert reg.profiles["local"].omo_profile == "oc-selector-local"

    def test_failure_malformed_unknown_key(self) -> None:
        data = (FIXTURES / "malformed.toml").read_text()
        with pytest.raises(RegistryError, match="unknown"):
            parse_registry(data)

    def test_failure_duplicate_directory(self) -> None:
        data = (FIXTURES / "collision.toml").read_text()
        with pytest.raises(RegistryError, match=r"[Dd]uplicate"):
            parse_registry(data)

    def test_happy_commented_registry_preserves_parse(self) -> None:
        data = (FIXTURES / "commented.toml").read_text()
        reg = parse_registry(data)
        assert reg.default_profile == "default"
        # Comments don't affect parsing

    def test_failure_missing_default(self) -> None:
        data = """\
schema_version = 1
default_profile = 'nonexistent'
opencode_executable = 'opencode'

[profiles.default]
directory = 'opencode'
"""
        with pytest.raises(RegistryError, match="not in profiles"):
            parse_registry(data)

    def test_failure_invalid_toml(self) -> None:
        with pytest.raises(RegistryError, match="parse"):
            parse_registry("this is not valid toml [[[")


# --- Profile name validation tests ---


class TestProfileNames:
    def test_happy_valid_names(self) -> None:
        for name in [
            "default",
            "gpt",
            "local",
            "standard",
            "my-profile",
            "a",
            "abc123",
        ]:
            assert validate_profile_name(name) == name

    def test_failure_reserved_management(self) -> None:
        for name in MANAGEMENT_NAMES:
            with pytest.raises(RegistryError, match="reserved"):
                validate_profile_name(name)

    def test_failure_reserved_native(self) -> None:
        for name in NATIVE_NAMES:
            with pytest.raises(RegistryError, match="reserved"):
                validate_profile_name(name)

    def test_failure_device_names(self) -> None:
        for name in ["con", "prn", "aux", "nul", "com1", "com9", "lpt1", "lpt9"]:
            with pytest.raises(RegistryError, match="reserved"):
                validate_profile_name(name)

    def test_failure_uppercase(self) -> None:
        with pytest.raises(RegistryError):
            validate_profile_name("MyProfile")

    def test_failure_starts_with_digit(self) -> None:
        with pytest.raises(RegistryError):
            validate_profile_name("1abc")

    def test_failure_too_long(self) -> None:
        with pytest.raises(RegistryError):
            validate_profile_name("a" * 49)

    def test_happy_max_length(self) -> None:
        # 1 + 47 = 48 chars max
        name = "a" * 48
        assert validate_profile_name(name) == name

    def test_failure_dot_segment(self) -> None:
        with pytest.raises(RegistryError):
            validate_profile_name("my.profile")

    def test_failure_separator(self) -> None:
        with pytest.raises(RegistryError):
            validate_profile_name("my/profile")


# --- Registry serialization roundtrip ---


class TestRegistryRoundtrip:
    def test_happy_roundtrip(self) -> None:
        data = (FIXTURES / "customized.toml").read_text()
        reg = parse_registry(data)
        output = registry_to_toml(reg)
        reg2 = parse_registry(output)
        assert reg2.default_profile == reg.default_profile
        assert set(reg2.profiles) == set(reg.profiles)
        for name in reg.profiles:
            assert reg2.profiles[name].directory == reg.profiles[name].directory
            assert reg2.profiles[name].omo_profile == reg.profiles[name].omo_profile

    def test_happy_roundtrip_model(self) -> None:
        reg = Registry(
            schema_version=1,
            default_profile="default",
            opencode_executable="opencode",
            profiles={
                "default": ProfileEntry(directory="opencode", model="openai/gpt-4"),
            },
        )
        output = registry_to_toml(reg)
        reg2 = parse_registry(output)
        assert reg2.profiles["default"].model == "openai/gpt-4"


# --- Registry model validation tests ---


class TestRegistryModelValidation:
    def test_happy_omo_profile_survives_model_dump(self) -> None:
        """omo_profile is preserved through ProfileEntry.model_dump()."""
        entry = ProfileEntry(directory="opencode", omo_profile="x")
        dumped = entry.model_dump()
        assert dumped["omo_profile"] == "x"

    def test_failure_schema_version_two_rejected(self) -> None:
        """schema_version != 1 raises a pydantic ValidationError."""
        with pytest.raises(ValidationError, match="schema_version"):
            Registry(
                schema_version=2,
                default_profile="default",
                profiles={"default": ProfileEntry(directory="opencode")},
            )

    def test_failure_empty_profiles_rejected(self) -> None:
        """A registry with zero profiles is rejected."""
        with pytest.raises(ValidationError, match="at least one profile"):
            Registry(default_profile="a", profiles={})

    def test_failure_executable_with_space_rejected(self) -> None:
        """A non-absolute executable containing a space is rejected."""
        with pytest.raises(ValidationError, match="opencode_executable"):
            Registry(
                default_profile="default",
                opencode_executable="my opencode",
                profiles={"default": ProfileEntry(directory="opencode")},
            )


# --- Registry TOML parse error tests ---


class TestRegistryParseErrors:
    def test_failure_schema_version_array_rejected(self) -> None:
        """A TOML array schema_version is rejected by _coerce_schema_version."""
        data = """\
schema_version = [1]
default_profile = "default"

[profiles.default]
directory = "opencode"
"""
        with pytest.raises(RegistryError, match="Unsupported schema_version"):
            parse_registry(data)

    def test_failure_profiles_not_a_table(self) -> None:
        """A scalar profiles value is rejected."""
        data = """\
default_profile = "default"
profiles = "not-a-table"
"""
        with pytest.raises(RegistryError, match="must be a table"):
            parse_registry(data)

    def test_failure_profile_entry_not_a_table(self) -> None:
        """A scalar entry under [profiles] is rejected."""
        data = """\
default_profile = "work"

[profiles]
work = "x"
"""
        with pytest.raises(RegistryError, match="is not a table"):
            parse_registry(data)

    def test_failure_unknown_profile_key(self) -> None:
        """An unknown key inside a profile table is rejected."""
        data = """\
default_profile = "default"

[profiles.default]
directory = "opencode"
unknown_key = "v"
"""
        with pytest.raises(RegistryError, match="Unknown profile key"):
            parse_registry(data)

    def test_failure_profile_missing_directory(self) -> None:
        """A profile table without a directory is rejected."""
        data = """\
default_profile = "default"

[profiles.default]
model = "openai/gpt-4"
"""
        with pytest.raises(RegistryError, match="missing required 'directory'"):
            parse_registry(data)


# --- Registry load tests ---


class TestLoadRegistry:
    def test_failure_missing_file(self, isolated_home: Path) -> None:
        """load_registry raises RegistryError for a missing path."""
        path = isolated_home / "missing" / "config.toml"
        with pytest.raises(RegistryError, match="Registry not found"):
            load_registry(path)


# --- Routing tests ---


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


class TestRouting:
    def test_happy_no_args_default(self) -> None:
        reg = _make_registry()
        r = route([], reg)
        assert isinstance(r, NativeRoute)
        assert r.profile_name == "default"
        assert r.argv == ()

    def test_happy_registered_profile(self) -> None:
        reg = _make_registry()
        r = route(["gpt", "--flag"], reg)
        assert isinstance(r, NativeRoute)
        assert r.profile_name == "gpt"
        assert r.argv == ("--flag",)

    def test_happy_unknown_token_native(self) -> None:
        reg = _make_registry()
        r = route(["unknown-cmd", "arg"], reg)
        assert isinstance(r, NativeRoute)
        assert r.profile_name == "default"
        assert r.argv == ("unknown-cmd", "arg")

    def test_happy_leading_separator(self) -> None:
        reg = _make_registry()
        r = route(["--", "run", "task"], reg)
        assert isinstance(r, NativeRoute)
        assert r.profile_name == "default"
        assert r.argv == ("run", "task")

    def test_happy_root_help_native(self) -> None:
        reg = _make_registry()
        r = route(["--help"], reg)
        assert isinstance(r, NativeRoute)
        assert r.profile_name is None
        assert r.argv == ("--help",)

    def test_happy_root_version_native(self) -> None:
        reg = _make_registry()
        r = route(["--version"], reg)
        assert isinstance(r, NativeRoute)
        assert r.profile_name is None
        assert r.argv == ("--version",)

    def test_happy_management_list(self) -> None:
        reg = _make_registry()
        r = route(["list"], reg)
        assert isinstance(r, ManagementRoute)
        assert r.command == "list"
        assert r.args == ()

    def test_happy_management_init_flags(self) -> None:
        reg = _make_registry()
        r = route(["init", "--no-backup", "--dry-run"], reg)
        assert isinstance(r, ManagementRoute)
        assert r.command == "init"
        assert r.args == ("--no-backup", "--dry-run")

    def test_happy_management_create(self) -> None:
        reg = _make_registry()
        r = route(["create", "newprofile"], reg)
        assert isinstance(r, ManagementRoute)
        assert r.command == "create"
        assert r.args == ("newprofile",)

    def test_happy_management_create_omo_profile(self) -> None:
        reg = _make_registry()
        r = route(["create", "a", "b", "--omo-profile", "x"], reg)
        assert isinstance(r, ManagementRoute)
        assert r.command == "create"
        assert r.args == ("a", "b", "--omo-profile", "x")

    def test_happy_profile_tail_preserved(self) -> None:
        reg = _make_registry()
        r = route(["gpt", "--", "run", "--help"], reg)
        assert isinstance(r, NativeRoute)
        assert r.profile_name == "gpt"
        assert r.argv == ("--", "run", "--help")

    def test_happy_no_registry_unknown(self) -> None:
        r = route(["something"], None)
        assert isinstance(r, NativeRoute)
        assert r.profile_name == "default"
        assert r.argv == ("something",)

    def test_happy_no_registry_empty(self) -> None:
        r = route([], None)
        assert isinstance(r, NativeRoute)
        assert r.profile_name == "default"
        assert r.argv == ()


# --- Management grammar validation tests ---


class TestManagementGrammar:
    def test_happy_list_json(self) -> None:
        validate_management_args("list", ("--json",))

    def test_happy_init_all_flags(self) -> None:
        validate_management_args("init", ("--no-backup", "--dry-run"))

    def test_happy_create_with_name(self) -> None:
        validate_management_args("create", ("myprofile", "--no-interactive"))

    def test_happy_set_default(self) -> None:
        validate_management_args("set-default", ("gpt",))

    def test_happy_sync_all(self) -> None:
        validate_management_args("sync", ("--all",))

    def test_happy_upgrade_omo_version(self) -> None:
        validate_management_args("upgrade-omo", ("--version", "5.2.0", "--all"))

    def test_happy_completion_shell(self) -> None:
        validate_management_args("completion", ("bash",))

    def test_happy_completion_alias(self) -> None:
        validate_management_args("completion", ("zsh", "--alias", "oc"))

    def test_failure_unknown_flag(self) -> None:
        with pytest.raises(ArgumentError, match="Unsupported"):
            validate_management_args("list", ("--unknown",))

    def test_failure_too_many_positionals(self) -> None:
        with pytest.raises(ArgumentError, match="Too many"):
            validate_management_args("list", ("extra",))

    def test_failure_profile_and_all(self) -> None:
        with pytest.raises(ArgumentError, match="both PROFILE and --all"):
            validate_management_args("sync", ("gpt", "--all"))

    def test_failure_recover_with_no_backup(self) -> None:
        with pytest.raises(ArgumentError, match="--recover"):
            validate_management_args("init", ("--recover", "--no-backup"))

    def test_failure_recover_with_dry_run(self) -> None:
        with pytest.raises(ArgumentError, match="--recover"):
            validate_management_args("init", ("--recover", "--dry-run"))

    def test_failure_invalid_profile_name(self) -> None:
        with pytest.raises(ArgumentError):
            validate_management_args("create", ("Invalid-Name",))

    def test_failure_version_requires_value(self) -> None:
        with pytest.raises(ArgumentError, match="requires a value"):
            validate_management_args("upgrade-omo", ("--version",))

    def test_happy_create_dir_and_model(self) -> None:
        validate_management_args(
            "create",
            ("myprofile", "mydir", "--model", "openai/gpt"),
        )

    def test_happy_edit_directory_and_model(self) -> None:
        validate_management_args(
            "edit",
            ("gpt", "--directory", "newdir", "--model", "anthropic/claude"),
        )

    def test_happy_sync_dry_run(self) -> None:
        validate_management_args("sync", ("--dry-run",))

    def test_happy_upgrade_omo_dry_run(self) -> None:
        validate_management_args("upgrade-omo", ("gpt", "--dry-run"))

    def test_failure_model_requires_value(self) -> None:
        with pytest.raises(ArgumentError, match="requires a value"):
            validate_management_args("create", ("myprofile", "--model"))

    def test_failure_directory_requires_value(self) -> None:
        with pytest.raises(ArgumentError, match="requires a value"):
            validate_management_args("edit", ("gpt", "--directory"))

    def test_failure_alias_requires_value(self) -> None:
        with pytest.raises(ArgumentError, match="requires a value"):
            validate_management_args("completion", ("bash", "--alias"))

    def test_failure_create_too_many_positionals(self) -> None:
        with pytest.raises(ArgumentError, match="Too many"):
            validate_management_args("create", ("a", "b", "c"))

    def test_failure_unknown_command(self) -> None:
        with pytest.raises(ArgumentError, match="Unknown management command"):
            validate_management_args("bogus", ())

    def test_failure_alias_empty_value(self) -> None:
        with pytest.raises(ArgumentError, match="requires a non-empty value"):
            validate_management_args("completion", ("bash", "--alias", ""))


class TestReservedNames:
    def test_happy_device_name_is_reserved(self) -> None:
        assert is_reserved_name("con") is True

    def test_happy_profile_name_not_reserved(self) -> None:
        assert is_reserved_name("work") is False
