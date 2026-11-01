"""Routing decisions for oc-selector.

Contract:
- Alias or canonical invocation has identical grammar.
- With no args, choose registry default.
- Inspect only first token before management parsing.
- A leading ``--`` removes exactly one token and forces default/native forwarding.
- A registered profile selects it and forwards its tail byte-for-byte.
- Unknown first tokens go unchanged to native default.
- Root ``--help`` and ``--version`` are native, not Typer options.
- No wrapper flags outside the management parser.

Route variants are immutable typed data; routing never invokes Typer or a child.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from oc_selector.errors import ArgumentError, RegistryError
from oc_selector.registry import MANAGEMENT_NAMES, RESERVED_NAMES, Registry, validate_profile_name

# --- Route variants ---


@dataclass(frozen=True, slots=True)
class NativeRoute:
    """Forward to native opencode with the given argv tail (byte-for-byte)."""

    argv: tuple[str, ...]
    profile_name: str | None = None  # None = default profile


@dataclass(frozen=True, slots=True)
class ManagementRoute:
    """A management command to be handled by the wrapper."""

    command: str
    args: tuple[str, ...] = field(default_factory=tuple)


# --- Routing logic ---


def route(argv: list[str], registry: Registry | None) -> NativeRoute | ManagementRoute:
    """Route an argv (excluding program name) to a Native or Management route.

    Rules:
    - Empty argv → NativeRoute with default profile, empty tail.
    - First token is ``--`` → remove it, force default/native forwarding of rest.
    - First token is a registered management name → ManagementRoute.
    - First token is a registered profile name → NativeRoute with that profile.
    - First token is ``--help`` or ``--version`` → NativeRoute (native handles).
    - Otherwise → NativeRoute with default profile (unknown token forwarded).
    """
    if not argv:
        # No args: use default profile
        default = registry.default_profile if registry else "default"
        return NativeRoute(argv=(), profile_name=default)

    first = argv[0]

    # Leading -- removes exactly one token, forces default/native forwarding
    if first == "--":
        tail = tuple(argv[1:])
        default = registry.default_profile if registry else "default"
        return NativeRoute(argv=tail, profile_name=default)

    # Root --help and --version are native
    if first in ("--help", "--version"):
        return NativeRoute(argv=tuple(argv), profile_name=None)

    # Management commands
    if first in MANAGEMENT_NAMES:
        return ManagementRoute(command=first, args=tuple(argv[1:]))

    # Registered profile selection
    if registry is not None and first in registry.profiles:
        tail = tuple(argv[1:])
        return NativeRoute(argv=tail, profile_name=first)

    # Unknown first token: forward unchanged to native default
    default = registry.default_profile if registry else "default"
    return NativeRoute(argv=tuple(argv), profile_name=default)


# --- Management grammar validation (exact grammar) ---

GRAMMAR_DOC = """\
list [--json]
info [--json]
init [--no-backup] [--dry-run] [--recover]
create NAME [DIRECTORY] [--model MODEL] [--no-interactive] [--dry-run]
edit PROFILE [--directory DIR] [--model MODEL] [--dry-run]
set-default PROFILE [--dry-run]
sync [PROFILE] [--all] [--dry-run]
upgrade-omo [PROFILE] [--version VERSION] [--all] [--dry-run]
completion SHELL [--alias NAME]
help [--version]
"""


@dataclass(frozen=True, slots=True)
class _CmdSpec:
    flags: frozenset[str]
    positionals: int


_GRAMMAR: dict[str, _CmdSpec] = {
    "list": _CmdSpec(frozenset({"--json"}), 0),
    "info": _CmdSpec(frozenset({"--json"}), 0),
    "init": _CmdSpec(frozenset({"--no-backup", "--dry-run", "--recover"}), 0),
    "create": _CmdSpec(frozenset({"--model", "--no-interactive", "--dry-run"}), 2),
    "edit": _CmdSpec(frozenset({"--directory", "--model", "--dry-run"}), 1),
    "set-default": _CmdSpec(frozenset({"--dry-run"}), 1),
    "sync": _CmdSpec(frozenset({"--all", "--dry-run"}), 1),
    "upgrade-omo": _CmdSpec(frozenset({"--version", "--all", "--dry-run"}), 1),
    "completion": _CmdSpec(frozenset({"--alias"}), 1),
    "help": _CmdSpec(frozenset({"--version"}), 0),
}

_VALUE_FLAGS: frozenset[str] = frozenset({"--version", "--alias", "--model", "--directory"})


def validate_management_args(command: str, args: tuple[str, ...]) -> None:  # noqa: C901, PLR0912
    """Validate management command arguments against the exact grammar.

    Raises ``ArgumentError`` (exit 2) on violation.
    """
    spec = _GRAMMAR.get(command)
    if spec is None:
        raise ArgumentError(f"Unknown management command: {command!r}")

    allowed_flags: frozenset[str] = spec.flags
    max_positionals: int = spec.positionals

    positionals: list[str] = []
    i = 0
    while i < len(args):
        tok = args[i]
        if tok.startswith("--"):
            # Check if it's a known flag
            if tok not in allowed_flags:
                raise ArgumentError(f"Unsupported option {tok!r} for {command!r}")
            if tok in _VALUE_FLAGS:
                i += 1
                if i >= len(args):
                    raise ArgumentError(f"Option {tok!r} requires a value")
                value = args[i]
                if not value:
                    raise ArgumentError(f"Option {tok!r} requires a non-empty value")
                if tok == "--alias":
                    validate_profile_name(value)
            if tok == "--recover" and ("--no-backup" in args or "--dry-run" in args):
                raise ArgumentError("--recover cannot combine with --no-backup or --dry-run")
        else:
            positionals.append(tok)
        i += 1

    if len(positionals) > max_positionals:
        raise ArgumentError(
            f"Too many arguments for {command!r}: got {len(positionals)}, max {max_positionals}",
        )

    # PROFILE + --all is error 2
    if command in ("sync", "upgrade-omo") and positionals and "--all" in args:
        raise ArgumentError(f"{command!r}: cannot specify both PROFILE and --all")

    # Validate profile names in positionals
    for pos in positionals:
        if command in ("create", "edit", "set-default", "sync", "upgrade-omo"):
            try:
                validate_profile_name(pos)
            except RegistryError as e:
                raise ArgumentError(str(e)) from e


def is_reserved_name(name: str) -> bool:
    """Check if a name is reserved (management or native)."""
    return name in RESERVED_NAMES
