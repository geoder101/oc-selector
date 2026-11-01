"""Typer management CLI and raw dispatch integration.

CLI routes management commands to typed handlers.
Raw dispatch forwards all non-management args to native opencode.
No author constants in installed src.
No silent fallbacks.
No network in CLI.
No secret material in logs.
No migration of legacy fields.
All mutations go through transaction.
Idempotent operations.
No conversion calls in CLI.
"""

from __future__ import annotations

import sys
from typing import Annotated

import typer

from oc_selector.completion import generate_completion
from oc_selector.diagnostics import collect_diagnostics
from oc_selector.errors import OcSelectorError
from oc_selector.init_flow import run_init, run_recovery_all
from oc_selector.paths import resolve_config_parent, state_root
from oc_selector.process import launch_native, resolve_executable
from oc_selector.registry import load_registry
from oc_selector.routing import NativeRoute
from oc_selector.wizard import (
    CreateProfileInput,
    EditProfileInput,
    run_create_wizard,
    run_edit_wizard,
    run_set_default,
)

app = typer.Typer(
    name="oc-selector",
    help="Select registered OpenCode settings while preserving native arguments.",
    no_args_is_help=True,
)


def _error_exit(message: str, code: int = 1) -> None:
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code)


@app.command()
def init() -> None:
    """Initialize oc-selector configuration."""
    try:
        result = run_init()
        if result.created_parent:
            typer.echo(f"Created config directory: {result.config_parent}")
        if result.created_registry:
            typer.echo(f"Created registry: {result.registry_path}")
        if result.already_initialized:
            typer.echo("Already initialized.")
    except OcSelectorError as exc:
        _error_exit(str(exc))


@app.command()
def create(
    name: Annotated[str, typer.Argument(help="Profile name")],
    directory: Annotated[str, typer.Argument(help="Profile directory")],
    model: Annotated[str | None, typer.Option("--model", "-m", help="Model ID")] = None,
) -> None:
    """Create a new profile."""
    parent = resolve_config_parent()
    reg_path = parent / "config.toml"
    inp = CreateProfileInput(name=name, directory=directory, model=model)
    result = run_create_wizard(inp, reg_path, state_root())
    if result.success:
        typer.echo(result.message)
    else:
        _error_exit(result.error or result.message)


@app.command()
def edit(
    name: Annotated[str, typer.Argument(help="Profile name")],
    directory: Annotated[str | None, typer.Option("--directory", "-d", help="New directory")] = None,
    model: Annotated[str | None, typer.Option("--model", "-m", help="New model ID")] = None,
) -> None:
    """Edit an existing profile."""
    parent = resolve_config_parent()
    reg_path = parent / "config.toml"
    inp = EditProfileInput(name=name, directory=directory, model=model)
    result = run_edit_wizard(inp, reg_path, state_root())
    if result.success:
        typer.echo(result.message)
    else:
        _error_exit(result.error or result.message)


@app.command("set-default")
def set_default_cmd(
    name: Annotated[str, typer.Argument(help="Profile name to set as default")],
) -> None:
    """Set the default profile (registry-only)."""
    parent = resolve_config_parent()
    reg_path = parent / "config.toml"
    result = run_set_default(name, reg_path, state_root())
    if result.success:
        typer.echo(result.message)
    else:
        _error_exit(result.error or result.message)


@app.command("list")
def list_profiles() -> None:
    """List all registered profiles."""
    parent = resolve_config_parent()
    reg_path = parent / "config.toml"
    if not reg_path.exists():
        _error_exit("No registry found. Run 'oc-selector init' first.")
    reg = load_registry(reg_path)
    for name, entry in reg.profiles.items():
        marker = "*" if name == reg.default_profile else " "
        typer.echo(f"{marker} {name} -> {entry.directory}")


@app.command()
def diagnostics() -> None:
    """Collect and display diagnostics."""
    parent = resolve_config_parent()
    reg_path = parent / "config.toml"
    reg = None
    if reg_path.exists():
        reg = load_registry(reg_path)
    result = collect_diagnostics(reg)
    typer.echo(f"OpenCode: {result.opencode.get('version', 'unknown')}")
    typer.echo(f"Default profile: {result.default_profile or 'none'}")
    typer.echo(f"Profiles: {len(result.profiles)}")
    for p in result.profiles:
        typer.echo(f"  {p.name}: {p.status}")


@app.command()
def completion(
    shell: Annotated[str, typer.Argument(help="Shell: bash, zsh, fish, powershell")],
) -> None:
    """Generate shell completion script."""
    parent = resolve_config_parent()
    reg_path = parent / "config.toml"
    reg = None
    if reg_path.exists():
        reg = load_registry(reg_path)
    script = generate_completion(shell, reg)
    typer.echo(script)


@app.command()
def recover() -> None:
    """Recover from pending transactions."""
    results = run_recovery_all()
    if not results:
        typer.echo("No pending recoveries.")
        return
    for r in results:
        if r.recovered:
            typer.echo(f"Recovered: {r.journal_id}")
        else:
            typer.echo(f"Failed: {r.journal_id} - {r.error}", err=True)


def main() -> None:
    """Route management commands or forward raw dispatch to native."""
    args = sys.argv[1:]

    # Check for management commands
    management_cmds = {
        "init",
        "create",
        "edit",
        "set-default",
        "list",
        "diagnostics",
        "completion",
        "recover",
    }
    if args and args[0] in management_cmds:
        app(args=args, prog_name="oc-selector")
        return

    # Raw dispatch: forward all args to native opencode
    try:
        parent = resolve_config_parent()
        reg_path = parent / "config.toml"
        if not reg_path.exists():
            _error_exit("No registry found. Run 'oc-selector init' first.", code=2)

        reg = load_registry(reg_path)
        resolve_executable(reg.opencode_executable)  # pre-validate (127 missing / 126 non-executable)
        decision = NativeRoute(argv=tuple(args), profile_name=reg.default_profile)
        launch_native(decision, reg)
    except OcSelectorError as exc:
        _error_exit(str(exc), code=2)


if __name__ == "__main__":
    main()
