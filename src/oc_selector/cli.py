"""Typer management CLI and raw dispatch integration.

Routes management commands to typed handlers and forwards every non-management
argv to native opencode byte-for-byte through the routing layer.

Invariants:
- No author constants in installed src.
- No silent fallbacks.
- No network in the CLI.
- No secret material in logs.
- No migration of legacy fields.
- All mutations go through a transaction.
- Idempotent operations.
- No conversion calls in the CLI.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path  # noqa: TC003
from typing import Annotated, NoReturn

import typer
from pydantic import TypeAdapter, ValidationError

from oc_selector import __version__
from oc_selector.agents_link import decide_link, ensure_link
from oc_selector.completion import extract_command_options, generate_completion
from oc_selector.diagnostics import collect_diagnostics, probe_version
from oc_selector.errors import OcSelectorError
from oc_selector.host_sync import sync_host_package
from oc_selector.init_flow import run_init, run_recovery_all
from oc_selector.omo_upgrade import OmoSchema, run_omo_upgrade
from oc_selector.paths import (
    default_profile_directory,
    display_profile_directory,
    global_agents_md_path,
    omo_config_path,
    registry_path,
    resolve_config_parent,
    resolve_profile_directory,
    state_root,
)
from oc_selector.process import launch_native
from oc_selector.registry import Registry, load_registry
from oc_selector.routing import GRAMMAR_DOC, ManagementRoute, route
from oc_selector.wizard import (
    CreateProfileInput,
    DeleteProfileInput,
    EditProfileInput,
    run_create_wizard,
    run_delete,
    run_edit_wizard,
    run_set_default,
)

app = typer.Typer(
    name="oc-selector",
    help="Select registered OpenCode settings while preserving native arguments.",
    no_args_is_help=True,
)

# Verified OpenCode 1.x release format; rejects dev/maintenance and 2.x.
_RELEASE_RE = re.compile(r"^1\.\d+\.\d+$")

# Normalizes parsed JSON values to plain typed dicts at the OMO config boundary.
_PLAIN_DICT_ADAPTER: TypeAdapter[dict[str, object]] = TypeAdapter(dict[str, object])
_PROMPT_ADAPTER: TypeAdapter[str] = TypeAdapter(str)


def _error_exit(message: str, code: int = 1) -> NoReturn:
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code)


def _is_1x_release(version: str) -> bool:
    """Return True if ``version`` is a verified 1.x release string."""
    return bool(_RELEASE_RE.match(version))


def _require_registry() -> tuple[Path, Registry]:
    """Load the registry or exit 2 with init guidance when it is absent."""
    parent = resolve_config_parent()
    reg_path = registry_path()
    if not reg_path.exists():
        _error_exit("No registry found. Run 'oc-selector init' first.", code=2)
    return parent, load_registry(reg_path)


def _run_recovery() -> None:
    """Recover all pending journals and report each outcome.

    Exits 1 if any journal fails to restore, so a partial recovery is never
    mistaken for success.
    """
    results = run_recovery_all()
    if not results:
        typer.echo("No pending recoveries.")
        return
    failed = False
    for entry in results:
        if entry.recovered:
            typer.echo(f"Recovered: {entry.journal_id}")
        else:
            typer.echo(f"Failed: {entry.journal_id} - {entry.error}", err=True)
            failed = True
    if failed:
        raise typer.Exit(1)


def _eligible_profiles(
    reg: Registry,
    profile: str | None,
    *,
    all_profiles: bool,
) -> list[str]:
    """Return the OMO-eligible profile names, or exit 2 on an invalid selection."""
    if all_profiles:
        return [
            name
            for name, entry in reg.profiles.items()
            if entry.omo_profile is not None
        ]
    target_name = profile or reg.default_profile
    if target_name not in reg.profiles:
        _error_exit(f"Profile {target_name!r} not found", code=2)
    if reg.profiles[target_name].omo_profile is None:
        name = target_name
        _error_exit(
            f"Profile {name!r} does not declare an OMO profile; nothing to upgrade",
            code=2,
        )
    return [target_name]


def _read_omo_config(path: Path) -> dict[str, object]:
    """Read and shape the OMO config JSON object, or exit 2 if it is invalid."""
    try:
        return _PLAIN_DICT_ADAPTER.validate_python(json.loads(path.read_text()))
    except json.JSONDecodeError as exc:
        _error_exit(f"Invalid OMO config at {path}: {exc}", code=2)
    except ValidationError:
        _error_exit(f"Invalid OMO config at {path}: expected a JSON object.", code=2)


def _resolve_target_version(reg: Registry, explicit: str | None) -> str:
    """Return the target OMO version from ``explicit`` or the host executable."""
    if explicit is not None:
        return explicit
    resolved, status = probe_version(reg.opencode_executable)
    if status != "ok" or resolved is None:
        exe = reg.opencode_executable
        _error_exit(
            f"Cannot resolve host version from {exe!r} (status: {status}).",
            code=2,
        )
    if not _is_1x_release(resolved):
        _error_exit(f"Unsupported host version for maintenance: {resolved!r}", code=2)
    return resolved


@app.command()
def init(
    *,
    no_backup: Annotated[
        bool,
        typer.Option("--no-backup", help="Skip backups (inert for init)"),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview without writing"),
    ] = False,
    recover: Annotated[
        bool,
        typer.Option("--recover", help="Recover pending transactions"),
    ] = False,
) -> None:
    """Initialize oc-selector configuration."""
    if recover and (no_backup or dry_run):
        _error_exit("--recover cannot combine with --no-backup or --dry-run", code=2)
    if recover:
        _run_recovery()
        return
    try:
        result = run_init(no_backup=no_backup, dry_run=dry_run)
    except OcSelectorError as exc:
        _error_exit(str(exc), code=2)
    verb = "Would create" if result.dry_run else "Created"
    if result.created_parent:
        typer.echo(f"{verb} config directory: {result.config_parent}")
    if result.created_registry:
        typer.echo(f"{verb} registry: {result.registry_path}")
    if result.already_initialized and not result.dry_run:
        typer.echo("Already initialized.")
    if result.dry_run and not result.created_parent and not result.created_registry:
        typer.echo("Nothing to create (already initialized).")


@app.command()
def create(  # noqa: PLR0913 - one parameter per CLI flag; the command surface is fixed.
    name: Annotated[str, typer.Argument(help="Profile name")],
    directory: Annotated[str | None, typer.Argument(help="Profile directory")] = None,
    *,
    model: Annotated[str | None, typer.Option("--model", "-m", help="Model ID")] = None,
    omo_profile: Annotated[
        str | None,
        typer.Option("--omo-profile", help="OMO profile name"),
    ] = None,
    no_interactive: Annotated[
        bool,
        typer.Option(
            "--no-interactive",
            help="Do not prompt; use the default directory",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview without writing"),
    ] = False,
) -> None:
    """Create a new profile."""
    _require_registry()
    if directory is None:
        default_dir = default_profile_directory(name)
        if no_interactive or not sys.stdin.isatty():
            directory = default_dir
        else:
            raw = _PROMPT_ADAPTER.validate_python(
                typer.prompt(f"Directory for profile {name!r}", default=default_dir),
            )
            directory = raw.strip() or default_dir
    inp = CreateProfileInput(
        name=name,
        directory=directory,
        model=model,
        omo_profile=omo_profile,
    )
    result = run_create_wizard(inp, registry_path(), state_root(), dry_run=dry_run)
    if result.success:
        suffix = f" ({result.agents_link})" if result.agents_link is not None else ""
        typer.echo(f"{result.message}{suffix}")
    else:
        _error_exit(result.error or result.message, code=result.exit_code)


@app.command()
def edit(
    name: Annotated[str, typer.Argument(help="Profile name")],
    *,
    directory: Annotated[
        str | None,
        typer.Option("--directory", "-d", help="New directory"),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option("--model", "-m", help="New model ID"),
    ] = None,
    omo_profile: Annotated[
        str | None,
        typer.Option("--omo-profile", help="New OMO profile name"),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview without writing"),
    ] = False,
) -> None:
    """Edit an existing profile."""
    _require_registry()
    inp = EditProfileInput(
        name=name,
        directory=directory,
        model=model,
        omo_profile=omo_profile,
    )
    result = run_edit_wizard(inp, registry_path(), state_root(), dry_run=dry_run)
    if result.success:
        suffix = f" ({result.agents_link})" if result.agents_link is not None else ""
        typer.echo(f"{result.message}{suffix}")
    else:
        _error_exit(result.error or result.message, code=result.exit_code)


@app.command("set-default")
def set_default_cmd(
    name: Annotated[str, typer.Argument(help="Profile name to set as default")],
    *,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview without writing"),
    ] = False,
) -> None:
    """Set the default profile (registry-only)."""
    _require_registry()
    result = run_set_default(name, registry_path(), state_root(), dry_run=dry_run)
    if result.success:
        typer.echo(result.message)
    else:
        _error_exit(result.error or result.message, code=result.exit_code)


@app.command()
def delete(
    name: Annotated[str, typer.Argument(help="Profile name")],
    *,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview without writing"),
    ] = False,
) -> None:
    """Delete a profile (registry entry only)."""
    _require_registry()
    inp = DeleteProfileInput(name=name)
    result = run_delete(inp, registry_path(), state_root(), dry_run=dry_run)
    if result.success:
        typer.echo(result.message)
    else:
        _error_exit(result.error or result.message, code=result.exit_code)


@app.command("list")
def list_profiles(
    *,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON"),
    ] = False,
) -> None:
    """List all registered profiles."""
    parent, reg = _require_registry()
    if json_output:
        payload = {
            "schema_version": 1,
            "default_profile": reg.default_profile,
            "profiles": [
                {
                    "name": profile_name,
                    "directory": display_profile_directory(parent, entry.directory),
                    "omo_profile": entry.omo_profile,
                }
                for profile_name, entry in reg.profiles.items()
            ],
        }
        typer.echo(json.dumps(payload))
        return
    for profile_name, entry in reg.profiles.items():
        marker = "*" if profile_name == reg.default_profile else " "
        suffix = f" ({entry.model})" if entry.model else ""
        resolved = display_profile_directory(parent, entry.directory)
        typer.echo(f"{marker} {profile_name} -> {resolved}{suffix}")


@app.command()
def info(
    *,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON"),
    ] = False,
) -> None:
    """Collect and display diagnostics for the current setup."""
    reg_path = registry_path()
    reg = load_registry(reg_path) if reg_path.exists() else None
    result = collect_diagnostics(reg)
    if json_output:
        typer.echo(json.dumps(result.to_dict()))
        return
    typer.echo(f"OpenCode: {result.opencode.get('version', 'unknown')}")
    typer.echo(f"Default profile: {result.default_profile or 'none'}")
    typer.echo(f"Profiles: {len(result.profiles)}")
    for profile in result.profiles:
        name_dir = f"{profile.name}: {profile.directory}"
        typer.echo(f"  {name_dir} ({profile.status}, AGENTS.md: {profile.agents_link})")


@app.command()
def sync(
    profile: Annotated[str | None, typer.Argument(help="Profile to sync")] = None,
    *,
    all_profiles: Annotated[
        bool,
        typer.Option("--all", help="Sync all profiles"),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview without running the package manager"),
    ] = False,
) -> None:
    """Sync the host package for selected profiles."""
    if profile is not None and all_profiles:
        _error_exit("cannot specify both PROFILE and --all", code=2)
    parent, reg = _require_registry()
    if all_profiles:
        targets = list(reg.profiles.items())
    else:
        target_name = profile or reg.default_profile
        if target_name not in reg.profiles:
            _error_exit(f"Profile {target_name!r} not found", code=2)
        targets = [(target_name, reg.profiles[target_name])]
    failed = False
    for target_name, entry in targets:
        result = sync_host_package(
            resolve_profile_directory(parent, entry.directory),
            state_root(),
            dry_run=dry_run,
        )
        if result.success:
            typer.echo(f"{target_name}: {result.message}")
        else:
            failed = True
            typer.echo(
                f"{target_name}: {result.message} ({result.error or 'error'})",
                err=True,
            )
    if failed:
        raise typer.Exit(1)


@app.command()
def link(
    profile: Annotated[str | None, typer.Argument(help="Profile to link")] = None,
    *,
    all_profiles: Annotated[
        bool,
        typer.Option("--all", help="Link all profiles"),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview without writing"),
    ] = False,
) -> None:
    """Link each profile's AGENTS.md to the global guard (idempotent).

    Warns and continues on a per-profile symlink failure; never aborts the loop
    and never fails the command for a link problem (exit stays 0). The registry
    is read-only here.
    """
    if profile is not None and all_profiles:
        _error_exit("cannot specify both PROFILE and --all", code=2)
    parent, reg = _require_registry()
    if all_profiles:
        targets = list(reg.profiles.items())
    else:
        target_name = profile or reg.default_profile
        if target_name not in reg.profiles:
            _error_exit(f"Profile {target_name!r} not found", code=2)
        targets = [(target_name, reg.profiles[target_name])]
    global_path = global_agents_md_path()
    for target_name, entry in targets:
        profile_dir = resolve_profile_directory(parent, entry.directory)
        if dry_run:
            result = decide_link(profile_dir, global_path)
            if result.status == "warn":
                typer.echo(f"{target_name}: warning: {result.reason}", err=True)
            else:
                typer.echo(f"{target_name}: would link")
            continue
        try:
            result = ensure_link(profile_dir, global_path)
        except OSError as exc:
            typer.echo(f"{target_name}: warning: {exc}", err=True)
            continue
        if result.status == "warn":
            typer.echo(f"{target_name}: warning: {result.reason}", err=True)
        else:
            typer.echo(f"{target_name}: linked")


@app.command("upgrade-omo")
def upgrade_omo_cmd(
    profile: Annotated[str | None, typer.Argument(help="Profile to upgrade")] = None,
    *,
    version: Annotated[
        str | None,
        typer.Option("--version", help="Target OMO version"),
    ] = None,
    all_profiles: Annotated[
        bool,
        typer.Option("--all", help="Upgrade all eligible profiles"),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview without writing"),
    ] = False,
) -> None:
    """Upgrade the OMO config to a target version."""
    if profile is not None and all_profiles:
        _error_exit("cannot specify both PROFILE and --all", code=2)
    _, reg = _require_registry()
    eligible = _eligible_profiles(reg, profile, all_profiles=all_profiles)
    if not eligible:
        typer.echo("No eligible profiles (none declare an OMO profile).")
        return
    omo_path = omo_config_path()
    if not omo_path.exists():
        _error_exit(f"OMO config not found at {omo_path}.", code=2)
    current = _read_omo_config(omo_path)
    target_version = _resolve_target_version(reg, version)
    schema = OmoSchema(version=target_version, required_fields=("version",))
    new_config: dict[str, object] = {**current, "version": target_version}
    result = run_omo_upgrade(
        omo_path,
        schema,
        state_root(),
        new_config=new_config,
        dry_run=dry_run,
    )
    if result.success:
        typer.echo(result.message)
    else:
        _error_exit(result.error or result.message)


@app.command()
def completion(
    shell: Annotated[str, typer.Argument(help="Shell: bash, zsh, fish, powershell")],
    alias: Annotated[
        str | None,
        typer.Option("--alias", help="Register under an alias name"),
    ] = None,
) -> None:
    """Generate a shell completion script."""
    reg_path = registry_path()
    reg = load_registry(reg_path) if reg_path.exists() else None
    command_options = extract_command_options(app)
    script = generate_completion(shell, reg, command_options, alias=alias)
    typer.echo(script)


@app.command("help")
def help_cmd(
    *,
    version: Annotated[
        bool,
        typer.Option("--version", help="Print the wrapper version"),
    ] = False,
) -> None:
    """Show wrapper usage and the management grammar."""
    if version:
        typer.echo(__version__)
        return
    typer.echo(GRAMMAR_DOC)


def main() -> None:
    """Route management commands to handlers or forward raw args to native."""
    args = sys.argv[1:]
    try:
        reg_path = registry_path()
        reg = (
            load_registry(reg_path)
            if reg_path.exists() and not (args and args[0] == "init")
            else None
        )
        decision = route(args, reg)
        if isinstance(decision, ManagementRoute):
            app(args=[decision.command, *decision.args], prog_name="oc-selector")
        else:
            if reg is None:
                _error_exit("No registry found. Run 'oc-selector init' first.", code=2)
            raise SystemExit(launch_native(decision, reg))
    except OcSelectorError as exc:
        # Honor the error class's own exit code (126/127/2/1); a plain message.
        typer.echo(f"Error: {exc}", err=True)
        raise SystemExit(exc.exit_code) from None
    except typer.Exit as exc:
        # Raised by the native branch (outside the app parser), which does not
        # catch it; convert to a clean SystemExit (no traceback, intended code).
        raise SystemExit(exc.exit_code) from None


if __name__ == "__main__":
    main()
