"""Guided create/edit wizard and registry-only set-default.

Guided create is interactive, validates all inputs.
Guided edit preserves existing fields, only modifies specified ones.
No author constants in installed src.
No silent fallbacks.
No network in wizard.
No secret material in logs.
No migration of legacy fields.
Set-default is registry-only, no file mutations.
All mutations go through transaction.
No conversion calls in wizard.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path  # noqa: TC003

from pydantic import ValidationError

from oc_selector.agents_link import LinkResult, ensure_link, link_path
from oc_selector.errors import (
    ArgumentError,
    RegistryError,
    SchemaValidationError,
)
from oc_selector.omo_profiles import validate_model_id
from oc_selector.paths import (
    global_agents_md_path,
    resolve_config_parent,
    resolve_profile_directory,
)
from oc_selector.registry import (
    ProfileEntry,
    Registry,
    load_registry,
    registry_to_toml,
    validate_profile_name,
)
from oc_selector.transactions import Transaction


@dataclass(frozen=True, slots=True)
class CreateProfileInput:
    """Typed input for guided profile creation."""

    name: str
    directory: str
    model: str | None = None
    omo_profile: str | None = None


@dataclass(frozen=True, slots=True)
class EditProfileInput:
    """Typed input for guided profile editing."""

    name: str
    directory: str | None = None
    model: str | None = None
    omo_profile: str | None = None


@dataclass(frozen=True, slots=True)
class DeleteProfileInput:
    """Typed input for registry-only profile deletion."""

    name: str


@dataclass(frozen=True, slots=True)
class WizardResult:
    """Result of a wizard operation."""

    success: bool
    profile_name: str
    message: str
    error: str | None = None
    # Public exit code for failure results (2 for usage errors, 1 otherwise).
    exit_code: int = 1
    # Human-readable fragment describing the profile->global AGENTS.md link
    # outcome (linked target / "link dangling: <target>" / "warning: <reason>"),
    # or None when no link step ran (e.g. dry run).
    agents_link: str | None = None


def validate_create_input(inp: CreateProfileInput) -> None:
    """Validate create profile input.

    Raises:
        ``SchemaValidationError`` on invalid input.

    """
    validate_profile_name(inp.name)
    if not inp.directory:
        # A missing/empty directory is a usage error (exit 2), not a schema fault.
        raise ArgumentError("Directory is required")
    if inp.model is not None:
        validate_model_id(inp.model)


def validate_edit_input(inp: EditProfileInput) -> None:
    """Validate edit profile input.

    Raises:
        ``SchemaValidationError`` on invalid input.

    """
    if inp.directory is not None and not inp.directory:
        raise ArgumentError("Directory cannot be empty")
    if inp.model is not None:
        validate_model_id(inp.model)


def _link_status_fragment(result: LinkResult, global_path: Path) -> str:
    """Build the ``agents_link`` message fragment from a link decision."""
    if result.status == "dangling":
        return f"link dangling: {global_path}"
    if result.status == "warn":
        return f"warning: {result.reason}"
    return str(global_path)


def _updated_registry(reg: Registry, name: str, entry: ProfileEntry) -> Registry:
    """Validate the prospective registry, including directory uniqueness."""
    try:
        return Registry(
            schema_version=reg.schema_version,
            default_profile=reg.default_profile,
            opencode_executable=reg.opencode_executable,
            profiles={**reg.profiles, name: entry},
        )
    except ValidationError as exc:
        raise RegistryError(str(exc)) from exc


def _edited_entry(existing: ProfileEntry, inp: EditProfileInput) -> ProfileEntry:
    """Preserve unspecified fields from the locked snapshot."""
    return ProfileEntry(
        directory=inp.directory if inp.directory is not None else existing.directory,
        model=inp.model if inp.model is not None else existing.model,
        omo_profile=inp.omo_profile
        if inp.omo_profile is not None
        else existing.omo_profile,
    )


def _current_registry(path: Path, name: str, operation: str) -> Registry:
    """Read and check mutation preconditions while the caller holds the lock."""
    reg = load_registry(path)
    if operation == "create":
        if name in reg.profiles:
            raise RegistryError(f"Profile {name!r} already exists")
    elif name not in reg.profiles:
        raise RegistryError(f"Profile {name!r} not found")
    elif operation == "delete" and name == reg.default_profile:
        raise RegistryError(f"Cannot delete default profile {name!r}")
    return reg


def _relink_after_directory_change(
    existing: ProfileEntry,
    inp: EditProfileInput,
) -> str | None:
    """Relink the profile's AGENTS.md when the directory changed.

    Returns the ``agents_link`` fragment, or ``None`` when the directory was
    unchanged (no link step ran). Runs AFTER the registry commit via the
    standalone primitive (warn-and-continue): a symlink failure yields a
    ``warning: <reason>`` fragment and never fails the edit.

    Half-move guard: the NEW link is created first; the OLD link is unlinked
    only once it is confirmed created (``status != "warn"``). On ``"warn"`` (a
    pre-existing real file, so the new link was not created) or an OSError, the
    OLD link is left intact.
    """
    if inp.directory is None or inp.directory == existing.directory:
        return None
    old_dir = resolve_profile_directory(resolve_config_parent(), existing.directory)
    new_dir = resolve_profile_directory(resolve_config_parent(), inp.directory)
    global_path = global_agents_md_path()
    try:
        link_result = ensure_link(new_dir, global_path)
    except OSError as exc:
        return f"warning: {exc}"
    if link_result.status != "warn":
        old_link = link_path(old_dir)
        if old_link.is_symlink():
            old_link.unlink()
    return _link_status_fragment(link_result, global_path)


def run_create_wizard(
    inp: CreateProfileInput,
    registry_path: Path,
    state_root: Path,
    *,
    dry_run: bool = False,
) -> WizardResult:
    """Run guided create wizard.

    Args:
        inp: Typed create input.
        registry_path: Path to the registry file.
        state_root: Path to the state root.
        dry_run: When set, validate and report the would-be creation without writing.

    Returns:
        A ``WizardResult`` describing the outcome.

    """
    try:
        validate_create_input(inp)
    except (ArgumentError, SchemaValidationError, RegistryError) as exc:
        # A missing/empty directory is a usage error (exit 2); a bad name or
        # model is an operation rejection (exit 1), per the verified exit-code
        # contract in docs/reference/exit-codes.rst.
        return WizardResult(
            success=False,
            profile_name=inp.name,
            message="Validation failed",
            error=str(exc),
            exit_code=2 if isinstance(exc, ArgumentError) else 1,
        )

    # Load existing registry
    try:
        reg = load_registry(registry_path)
        _updated_registry(
            reg,
            inp.name,
            ProfileEntry(
                directory=inp.directory, model=inp.model, omo_profile=inp.omo_profile
            ),
        )
    except (RegistryError, ValidationError) as exc:
        return WizardResult(
            success=False,
            profile_name=inp.name,
            message="Cannot load registry",
            error=str(exc),
        )

    # Check for duplicate
    if inp.name in reg.profiles:
        return WizardResult(
            success=False,
            profile_name=inp.name,
            message="Profile already exists",
            error=f"Profile {inp.name!r} already exists",
        )

    if dry_run:
        return WizardResult(
            success=True,
            profile_name=inp.name,
            message=f"Dry run: would create profile {inp.name!r} -> {inp.directory}",
        )

    # Create new entry
    entry = ProfileEntry(
        directory=inp.directory,
        model=inp.model,
        omo_profile=inp.omo_profile,
    )

    # Use transaction for mutation
    tx = Transaction(state_root, operation="create")
    link_fragment: str | None = None
    try:
        tx.begin()
        reg = _current_registry(registry_path, inp.name, "create")
        new_reg = _updated_registry(reg, inp.name, entry)
        new_toml = registry_to_toml(new_reg)
        # Write to temp file, then stage it
        tmp_file = state_root / "staging" / tx.id / "new_config.toml"
        tmp_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_file.write_text(new_toml)
        tx.stage_file(tmp_file, registry_path)
        tx.commit()
        # Link the profile's AGENTS.md to the global guard. This runs AFTER the
        # registry commit, via the standalone primitive (NOT the registry
        # transaction), so a symlink failure never fails the create and the
        # registry is never left half-written.
        profile_dir = resolve_profile_directory(resolve_config_parent(), inp.directory)
        global_path = global_agents_md_path()
        try:
            link_result = ensure_link(profile_dir, global_path)
        except OSError as exc:
            link_fragment = f"warning: {exc}"
        else:
            link_fragment = _link_status_fragment(link_result, global_path)
    except (OSError, RegistryError) as exc:
        tx.rollback()
        return WizardResult(
            success=False,
            profile_name=inp.name,
            message="Transaction failed",
            error=str(exc),
        )
    finally:
        tx.close()

    return WizardResult(
        success=True,
        profile_name=inp.name,
        message=f"Created profile {inp.name!r}",
        agents_link=link_fragment,
    )


def run_edit_wizard(
    inp: EditProfileInput,
    registry_path: Path,
    state_root: Path,
    *,
    dry_run: bool = False,
) -> WizardResult:
    """Run guided edit wizard.

    Args:
        inp: Typed edit input.
        registry_path: Path to the registry file.
        state_root: Path to the state root.
        dry_run: When set, validate and report the would-be edit without writing.

    Returns:
        A ``WizardResult`` describing the outcome.

    """
    # Load existing registry
    try:
        reg = load_registry(registry_path)
    except RegistryError as exc:
        return WizardResult(
            success=False,
            profile_name=inp.name,
            message="Cannot load registry",
            error=str(exc),
        )

    # Check profile exists
    if inp.name not in reg.profiles:
        return WizardResult(
            success=False,
            profile_name=inp.name,
            message="Profile not found",
            error=f"Profile {inp.name!r} not found",
        )

    existing = reg.profiles[inp.name]

    try:
        validate_edit_input(inp)
        _updated_registry(reg, inp.name, _edited_entry(existing, inp))
    except (
        ArgumentError,
        SchemaValidationError,
        RegistryError,
        ValidationError,
    ) as exc:
        # A missing/empty directory is a usage error (exit 2); any other
        # failure (bad model, etc.) is an operation rejection (exit 1).
        return WizardResult(
            success=False,
            profile_name=inp.name,
            message="Validation failed",
            error=str(exc),
            exit_code=2 if isinstance(exc, ArgumentError) else 1,
        )

    if dry_run:
        new_dir = inp.directory if inp.directory is not None else existing.directory
        new_model = inp.model if inp.model is not None else existing.model
        return WizardResult(
            success=True,
            profile_name=inp.name,
            message=(
                f"Dry run: would edit profile {inp.name!r} -> directory={new_dir}, "
                f"model={new_model}"
            ),
        )

    # Build updated entry
    # Use transaction for mutation
    tx = Transaction(state_root, operation="edit")
    link_fragment: str | None = None
    try:
        tx.begin()
        reg = _current_registry(registry_path, inp.name, "edit")
        existing = reg.profiles[inp.name]
        new_reg = _updated_registry(reg, inp.name, _edited_entry(existing, inp))
        new_toml = registry_to_toml(new_reg)
        tmp_file = state_root / "staging" / tx.id / "new_config.toml"
        tmp_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_file.write_text(new_toml)
        tx.stage_file(tmp_file, registry_path)
        tx.commit()
        # Relink the profile's AGENTS.md to the global guard when the
        # directory changed. This runs AFTER the registry commit, via the
        # standalone primitive (NOT the registry transaction), so a symlink
        # failure never fails the edit and the registry is never left
        # half-written.
        link_fragment = _relink_after_directory_change(existing, inp)
    except (OSError, RegistryError) as exc:
        tx.rollback()
        return WizardResult(
            success=False,
            profile_name=inp.name,
            message="Transaction failed",
            error=str(exc),
        )
    finally:
        tx.close()

    return WizardResult(
        success=True,
        profile_name=inp.name,
        message=f"Edited profile {inp.name!r}",
        agents_link=link_fragment,
    )


def run_delete(  # noqa: PLR0911
    inp: DeleteProfileInput,
    registry_path: Path,
    state_root: Path,
    *,
    dry_run: bool = False,
) -> WizardResult:
    """Run registry-only profile deletion.

    Deletes ONLY the registry entry for a profile; never touches any profile
    directory or ``~/.omo``. All mutations go through transaction.

    Args:
        inp: Typed delete input.
        registry_path: Path to the registry file.
        state_root: Path to the state root.
        dry_run: When set, validate and report the would-be deletion without writing.

    Returns:
        A ``WizardResult`` describing the outcome.

    """
    # Load existing registry
    try:
        reg = load_registry(registry_path)
    except RegistryError as exc:
        return WizardResult(
            success=False,
            profile_name=inp.name,
            message="Cannot load registry",
            error=str(exc),
        )

    # Check profile exists
    if inp.name not in reg.profiles:
        return WizardResult(
            success=False,
            profile_name=inp.name,
            message="Profile not found",
            error=f"Profile {inp.name!r} not found",
        )

    # Refuse to delete the default profile
    if inp.name == reg.default_profile:
        return WizardResult(
            success=False,
            profile_name=inp.name,
            message="Cannot delete default profile",
            error=(
                f"Cannot delete default profile {inp.name!r}; "
                f"run 'oc-selector set-default <other>' first."
            ),
        )

    # Refuse to delete the last remaining profile
    if len(reg.profiles) == 1:
        return WizardResult(
            success=False,
            profile_name=inp.name,
            message="Cannot delete last remaining profile",
            error=f"Cannot delete the last remaining profile {inp.name!r}.",
        )

    if dry_run:
        return WizardResult(
            success=True,
            profile_name=inp.name,
            message=f"Dry run: would delete profile {inp.name!r}",
        )

    # Use transaction for mutation
    tx = Transaction(state_root, operation="delete")
    try:
        tx.begin()
        reg = _current_registry(registry_path, inp.name, "delete")
        remaining = {
            name: entry for name, entry in reg.profiles.items() if name != inp.name
        }
        new_reg = Registry(
            schema_version=reg.schema_version,
            default_profile=reg.default_profile,
            opencode_executable=reg.opencode_executable,
            profiles=remaining,
        )
        new_toml = registry_to_toml(new_reg)
        # Write to temp file, then stage it
        tmp_file = state_root / "staging" / tx.id / "new_config.toml"
        tmp_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_file.write_text(new_toml)
        tx.stage_file(tmp_file, registry_path)
        tx.commit()
    except (OSError, RegistryError) as exc:
        tx.rollback()
        return WizardResult(
            success=False,
            profile_name=inp.name,
            message="Transaction failed",
            error=str(exc),
        )
    finally:
        tx.close()

    return WizardResult(
        success=True,
        profile_name=inp.name,
        message=f"Deleted profile {inp.name!r}",
    )


def run_set_default(
    profile_name: str,
    registry_path: Path,
    state_root: Path,
    *,
    dry_run: bool = False,
) -> WizardResult:
    """Run registry-only set-default operation.

    Args:
        profile_name: Name of the profile to set as default.
        registry_path: Path to the registry file.
        state_root: Path to the state root.
        dry_run: When set, validate and report the would-be change without writing.

    Returns:
        A ``WizardResult`` describing the outcome.

    """
    # Load existing registry
    try:
        reg = load_registry(registry_path)
    except RegistryError as exc:
        return WizardResult(
            success=False,
            profile_name=profile_name,
            message="Cannot load registry",
            error=str(exc),
        )

    # Check profile exists
    if profile_name not in reg.profiles:
        return WizardResult(
            success=False,
            profile_name=profile_name,
            message="Profile not found",
            error=f"Profile {profile_name!r} not found",
        )

    if dry_run:
        return WizardResult(
            success=True,
            profile_name=profile_name,
            message=f"Dry run: would set default to {profile_name!r}",
        )

    # Use transaction for mutation
    tx = Transaction(state_root, operation="set-default")
    try:
        tx.begin()
        reg = _current_registry(registry_path, profile_name, "set-default")
        new_reg = Registry(
            schema_version=reg.schema_version,
            default_profile=profile_name,
            opencode_executable=reg.opencode_executable,
            profiles=reg.profiles,
        )
        new_toml = registry_to_toml(new_reg)
        tmp_file = state_root / "staging" / tx.id / "new_config.toml"
        tmp_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_file.write_text(new_toml)
        tx.stage_file(tmp_file, registry_path)
        tx.commit()
    except (OSError, RegistryError) as exc:
        tx.rollback()
        return WizardResult(
            success=False,
            profile_name=profile_name,
            message="Transaction failed",
            error=str(exc),
        )
    finally:
        tx.close()

    return WizardResult(
        success=True,
        profile_name=profile_name,
        message=f"Set default to {profile_name!r}",
    )
