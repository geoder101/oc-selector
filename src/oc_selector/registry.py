"""Typed registry model for oc-selector.

Parses and validates the ``config.toml`` registry file using tomlkit (for
comment preservation) and Pydantic v2 (for typed validation).

Contract:
- Schema version 1, default_profile, opencode_executable, profiles table.
- Profile names: ``[a-z][a-z0-9-]{0,47}``, no casefold collisions, no device names.
- Reserved management and native names rejected.
- Default must reference an existing profile.
- Duplicate directory mappings rejected.
- Unknown keys rejected.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, ClassVar, Self

if TYPE_CHECKING:
    from pathlib import Path

import tomlkit
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_serializer, model_validator

from oc_selector.errors import RegistryError

# Normalizes tomlkit mappings (unparameterized in stubs) to plain typed dicts.
_PLAIN_DICT_ADAPTER: TypeAdapter[dict[str, object]] = TypeAdapter(dict[str, object])

# --- Name validation constants ---

_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,47}$")

# Reserved management tokens
MANAGEMENT_NAMES: frozenset[str] = frozenset(
    {
        "list",
        "init",
        "create",
        "edit",
        "set-default",
        "sync",
        "upgrade-omo",
        "completion",
        "help",
        "info",
    },
)

# Native safe superset - union with the pinned actual command names from the upstream fixture
NATIVE_NAMES: frozenset[str] = frozenset(
    {
        "completion",
        "acp",
        "mcp",
        "attach",
        "run",
        "debug",
        "auth",
        "agent",
        "upgrade",
        "uninstall",
        "serve",
        "web",
        "models",
        "stats",
        "export",
        "import",
        "github",
        "pr",
        "session",
        "db",
    },
)

# Windows device names (rejected on all platforms)
_DEVICE_NAMES: frozenset[str] = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        "conin$",
        "conout$",
    }
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)},
)

RESERVED_NAMES: frozenset[str] = MANAGEMENT_NAMES | NATIVE_NAMES | _DEVICE_NAMES


def validate_profile_name(name: str) -> str:
    """Validate a profile name against the naming rules. Returns the name if valid."""
    if not _NAME_RE.fullmatch(name):
        raise RegistryError(f"Invalid profile name: {name!r} (must match [a-z][a-z0-9-]{{0,47}})")
    if name in RESERVED_NAMES:
        raise RegistryError(f"Profile name {name!r} is reserved")
    # Reject dot segments and path separators
    if "." in name or "/" in name or "\\" in name:
        raise RegistryError(f"Profile name contains invalid characters: {name!r}")
    # Reject absolute paths
    if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
        raise RegistryError(f"Profile name is an absolute path: {name!r}")
    return name


def _check_casefold_collision(name: str, existing: set[str]) -> None:
    """Check for casefold collision with existing names."""
    cf = name.casefold()
    for other in existing:
        if other.casefold() == cf and other != name:
            raise RegistryError(f"Casefold collision: {name!r} conflicts with {other!r}")


# --- Pydantic models ---


class ProfileEntry(BaseModel):
    """A single profile entry in the registry."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)

    directory: str = Field(min_length=1)
    model: str | None = None
    omo_profile: str | None = None

    @field_serializer("omo_profile")
    def _serialize_omo(self, v: str | None) -> str | None:
        return v


class Registry(BaseModel):
    """The full oc-selector registry (config.toml)."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True, strict=True, validate_default=True)

    schema_version: int = Field(default=1)
    default_profile: str
    opencode_executable: str = Field(default="opencode")
    profiles: dict[str, ProfileEntry] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if self.schema_version != 1:
            raise ValueError(f"Unsupported schema_version: {self.schema_version}")
        if not self.profiles:
            raise ValueError("Registry must have at least one profile")
        # Default must exist
        if self.default_profile not in self.profiles:
            raise ValueError(f"default_profile {self.default_profile!r} not in profiles")
        # Validate names
        seen: set[str] = set()
        for name in self.profiles:
            validate_profile_name(name)
            _check_casefold_collision(name, seen)
            seen.add(name)
        # Reject duplicate directory mappings
        dirs: dict[str, str] = {}
        for name, entry in self.profiles.items():
            if entry.directory in dirs:
                raise ValueError(
                    f"Duplicate directory {entry.directory!r} for profiles {dirs[entry.directory]!r} and {name!r}",
                )
            dirs[entry.directory] = name
        # Executable must be a command name or absolute path, not a shell string
        exe = self.opencode_executable
        if " " in exe and not exe.startswith("/"):
            raise ValueError(f"opencode_executable must be a command or absolute path: {exe!r}")
        return self


# --- TOML I/O ---


def parse_registry(data: str) -> Registry:
    """Parse registry TOML text into a validated Registry.

    Raises ``RegistryError`` on parse or validation failure.
    """
    try:
        doc = tomlkit.parse(data)
    except Exception as e:
        raise RegistryError(f"Failed to parse registry TOML: {e}") from e
    return _doc_to_registry(doc)


def _coerce_schema_version(raw: object) -> int:
    """Coerce a raw schema_version value to int, preserving int() semantics."""
    if isinstance(raw, int | str | float):
        return int(raw)
    raise RegistryError(f"Unsupported schema_version: {raw!r}")


def _doc_to_registry(doc: tomlkit.TOMLDocument) -> Registry:
    """Convert a tomlkit document to a validated Registry."""
    typed_doc: dict[str, object] = _PLAIN_DICT_ADAPTER.validate_python(doc)
    # Check for unknown top-level keys
    known_keys = {"schema_version", "default_profile", "opencode_executable", "profiles"}
    for key in typed_doc:
        if key not in known_keys:
            raise RegistryError(f"Unknown registry key: {key!r}")
    # Extract profiles
    raw_profiles = typed_doc.get("profiles", {})
    if not isinstance(raw_profiles, dict):
        raise RegistryError("Registry 'profiles' must be a table")
    profiles_doc: dict[str, object] = _PLAIN_DICT_ADAPTER.validate_python(raw_profiles)
    profiles: dict[str, ProfileEntry] = {}
    for name, raw_entry in profiles_doc.items():
        if not isinstance(raw_entry, dict):
            raise RegistryError(f"Profile {name!r} is not a table")
        entry_doc: dict[str, object] = _PLAIN_DICT_ADAPTER.validate_python(raw_entry)
        known_profile_keys = {"directory", "omo_profile", "model"}
        kwargs: dict[str, str | None] = {}
        for key, val in entry_doc.items():
            if key not in known_profile_keys:
                raise RegistryError(f"Unknown profile key {key!r} in profile {name!r}")
            kwargs[key] = str(val) if val is not None else None
        if "directory" not in kwargs or not kwargs["directory"]:
            raise RegistryError(f"Profile {name!r} missing required 'directory'")
        profiles[name] = ProfileEntry(
            directory=kwargs["directory"],
            model=kwargs.get("model"),
            omo_profile=kwargs.get("omo_profile"),
        )
    schema_version_raw = typed_doc.get("schema_version", 1)
    default_profile_raw = typed_doc.get("default_profile", "")
    executable_raw = typed_doc.get("opencode_executable", "opencode")
    try:
        return Registry(
            schema_version=_coerce_schema_version(schema_version_raw),
            default_profile=str(default_profile_raw),
            opencode_executable=str(executable_raw),
            profiles=profiles,
        )
    except ValueError as e:
        raise RegistryError(str(e)) from e


def registry_to_toml(reg: Registry) -> str:
    """Serialize a Registry back to TOML (for writing)."""
    doc: tomlkit.TOMLDocument = tomlkit.document()
    doc["schema_version"] = reg.schema_version
    doc["default_profile"] = reg.default_profile
    doc["opencode_executable"] = reg.opencode_executable
    profiles_table = tomlkit.table()
    for name, entry in reg.profiles.items():
        ptable = tomlkit.table()
        ptable["directory"] = entry.directory
        if entry.model is not None:
            ptable["model"] = entry.model
        if entry.omo_profile is not None:
            ptable["omo_profile"] = entry.omo_profile
        profiles_table[name] = ptable
    if profiles_table:
        doc["profiles"] = profiles_table
    return tomlkit.dumps(doc)


def load_registry(path: Path) -> Registry:
    """Load and validate a registry from a file path."""
    if not path.exists():
        raise RegistryError(f"Registry not found: {path}")
    data = path.read_text(encoding="utf-8")
    return parse_registry(data)
