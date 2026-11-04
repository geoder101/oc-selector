"""TDD tests for non-mutating four-shell completion.

Covers:
- All four shells generate valid scripts
- Profile names included in completion
- Management commands included
- Option switches derived from the Typer app and embedded per command
- Alias validation
- Unsupported shell rejected
- No file I/O, no subprocess
- Absent registry yields management-only completion
"""

from __future__ import annotations

from pathlib import Path  # noqa: TC003
from typing import Annotated

import pytest
import typer

from oc_selector.completion import (
    extract_command_options,
    generate_completion,
    validate_alias,
    validate_shell,
)
from oc_selector.errors import ArgumentError
from oc_selector.registry import ProfileEntry, Registry

# A realistic command->switches mapping, shaped like extract_command_options output.
_OPTIONS: dict[str, list[str]] = {
    "init": ["--no-backup", "--dry-run", "--recover"],
    "create": ["--model", "-m", "--omo-profile", "--no-interactive", "--dry-run"],
    "edit": ["--directory", "-d", "--model", "-m", "--omo-profile", "--dry-run"],
    "set-default": ["--dry-run"],
    "delete": ["--dry-run"],
    "list": ["--json"],
    "info": ["--json"],
    "sync": ["--all", "--dry-run"],
    "upgrade-omo": ["--version", "--all", "--dry-run"],
    "completion": ["--alias"],
    "help": ["--version"],
}


def _make_registry() -> Registry:
    return Registry(
        schema_version=1,
        default_profile="default",
        profiles={
            "default": ProfileEntry(directory="opencode"),
            "gpt": ProfileEntry(directory="opencode_gpt"),
            "local": ProfileEntry(directory="opencode_local"),
        },
    )


def _tiny_app() -> typer.Typer:
    app = typer.Typer()

    @app.command()
    def make_thing(
        _name: Annotated[str, typer.Argument(help="Thing name")],
        *,
        _fast: Annotated[bool, typer.Option("--fast", help="Fast mode")] = False,
        _quiet: Annotated[
            str | None,
            typer.Option("--quiet", "-q", help="Quiet"),
        ] = None,
    ) -> None:
        """Make a thing."""

    return app


class TestValidateShell:
    def test_happy_all_shells(self) -> None:
        for shell in ("bash", "zsh", "fish", "powershell"):
            assert validate_shell(shell) == shell

    def test_failure_unsupported(self) -> None:
        with pytest.raises(ArgumentError, match="Unsupported"):
            validate_shell("tcsh")


class TestValidateAlias:
    def test_happy_valid_alias(self) -> None:
        assert validate_alias("oc") == "oc"

    def test_failure_invalid_alias(self) -> None:
        with pytest.raises(ArgumentError):
            validate_alias("Invalid-Name")


class TestExtractCommandOptions:
    def test_happy_options_only_no_positionals(self) -> None:
        options = extract_command_options(_tiny_app())
        assert options == {"make-thing": ["--fast", "--quiet", "-q"]}

    def test_happy_explicit_command_name(self) -> None:
        app = typer.Typer()

        @app.command("go")
        def runner(
            *,
            _verbose: Annotated[
                bool,
                typer.Option("--verbose", help="Verbose"),
            ] = False,
        ) -> None:
            """Run the thing."""

        assert extract_command_options(app) == {"go": ["--verbose"]}

    def test_happy_no_file_writes(self, tmp_path: Path) -> None:
        before = set(tmp_path.iterdir())
        extract_command_options(_tiny_app())
        after = set(tmp_path.iterdir())
        assert before == after


class TestGenerateCompletion:
    def test_happy_bash(self) -> None:
        reg = _make_registry()
        script = generate_completion("bash", reg, _OPTIONS)
        assert "oc-selector" in script
        assert "default" in script
        assert "gpt" in script
        assert "local" in script
        assert "complete" in script

    def test_happy_zsh(self) -> None:
        reg = _make_registry()
        script = generate_completion("zsh", reg, _OPTIONS)
        assert "oc-selector" in script
        assert "compadd" in script
        assert "default" in script

    def test_happy_fish(self) -> None:
        reg = _make_registry()
        script = generate_completion("fish", reg, _OPTIONS)
        assert "oc-selector" in script
        assert "complete" in script
        assert "default" in script

    def test_happy_powershell(self) -> None:
        reg = _make_registry()
        script = generate_completion("powershell", reg, _OPTIONS)
        assert "oc-selector" in script
        assert "Register-ArgumentCompleter" in script
        assert "default" in script

    def test_happy_with_alias(self) -> None:
        reg = _make_registry()
        script = generate_completion("bash", reg, _OPTIONS, alias="oc")
        assert "oc" in script
        assert "complete" in script

    def test_happy_no_registry(self) -> None:
        script = generate_completion("bash", None, _OPTIONS)
        assert "oc-selector" in script
        # Management commands still present
        assert "list" in script
        assert "init" in script

    def test_happy_management_commands_present(self) -> None:
        reg = _make_registry()
        for shell in ("bash", "zsh", "fish", "powershell"):
            script = generate_completion(shell, reg, _OPTIONS)
            assert "list" in script
            assert "init" in script
            assert "create" in script
            assert "set-default" in script

    def test_happy_delete_in_all_shells(self) -> None:
        reg = _make_registry()
        for shell in ("bash", "zsh", "fish", "powershell"):
            script = generate_completion(shell, reg, _OPTIONS)
            assert "delete" in script

    def test_happy_switches_present_in_all_shells(self) -> None:
        reg = _make_registry()
        for shell in ("bash", "zsh", "fish", "powershell"):
            script = generate_completion(shell, reg, _OPTIONS)
            assert "--dry-run" in script
            assert "--model" in script
            assert "-m" in script
            assert "--all" in script
            assert "--json" in script
            assert "--alias" in script

    def test_happy_switches_grouped_under_their_command(self) -> None:
        reg = _make_registry()
        script = generate_completion("bash", reg, _OPTIONS)
        # The create branch lists create's switches after its profile candidates.
        create_branch = _bash_branch(script, "create")
        assert "default gpt local" in create_branch
        assert "--model" in create_branch
        assert "-m" in create_branch
        # The init branch (no positional) lists only its switches.
        init_branch = _bash_branch(script, "init")
        assert "--no-backup" in init_branch
        assert "--recover" in init_branch
        assert "default" not in init_branch

    def test_happy_completion_offers_shells_plus_alias(self) -> None:
        reg = _make_registry()
        script = generate_completion("bash", reg, _OPTIONS)
        completion_branch = _bash_branch(script, "completion")
        assert "bash zsh fish powershell" in completion_branch
        assert "--alias" in completion_branch

    def test_happy_no_switches_when_mapping_empty(self) -> None:
        reg = _make_registry()
        script = generate_completion("bash", reg, {})
        assert "--dry-run" not in script
        # Profile candidates for profile commands are still offered.
        create_branch = _bash_branch(script, "create")
        assert "default gpt local" in create_branch

    def test_happy_help_offered_in_all_shells(self) -> None:
        reg = _make_registry()
        for shell in ("bash", "zsh", "fish", "powershell"):
            script = generate_completion(shell, reg, _OPTIONS)
            assert "--help" in script

    def test_happy_help_offered_on_a_declared_switch_command(self) -> None:
        reg = _make_registry()
        script = generate_completion("bash", reg, _OPTIONS)
        info_branch = _bash_branch(script, "info")
        assert "--json" in info_branch
        assert "--help" in info_branch

    def test_happy_help_still_offered_when_mapping_empty(self) -> None:
        """The Typer-injected ``--help`` keeps a command's branch alive.

        Regression: a command with no declared switches (only ``--help``) used
        to produce no completion branch, so ``oc-selector info --he`` completed
        nothing.
        """
        reg = _make_registry()
        script = generate_completion("bash", reg, {})
        info_branch = _bash_branch(script, "info")
        assert "--help" in info_branch

    def test_failure_unsupported_shell(self) -> None:
        reg = _make_registry()
        with pytest.raises(ArgumentError):
            generate_completion("tcsh", reg, _OPTIONS)

    def test_happy_no_file_writes(self, tmp_path: Path) -> None:
        """Completion generation must not write any files."""
        reg = _make_registry()
        before = set(tmp_path.iterdir())
        generate_completion("bash", reg, _OPTIONS)
        after = set(tmp_path.iterdir())
        assert before == after


class TestNativeDelegation:
    def test_happy_hook_in_all_shells(self) -> None:
        reg = _make_registry()
        for shell in ("bash", "zsh", "fish", "powershell"):
            script = generate_completion(shell, reg, _OPTIONS)
            assert "--get-yargs-completions" in script

    def test_happy_zero_placeholder_filtered(self) -> None:
        reg = _make_registry()
        for shell in ("bash", "zsh", "fish"):
            script = generate_completion(shell, reg, _OPTIONS)
            assert "grep -v -x -F -- '$0'" in script
        script = generate_completion("powershell", reg, _OPTIONS)
        assert "-ne '$0'" in script

    def test_happy_bakes_registry_executable(self) -> None:
        reg = Registry(
            schema_version=1,
            default_profile="default",
            opencode_executable="/opt/opencode/bin/opencode",
            profiles={"default": ProfileEntry(directory="opencode")},
        )
        script = generate_completion("bash", reg, _OPTIONS)
        assert "/opt/opencode/bin/opencode" in script

    def test_happy_default_executable_without_registry(self) -> None:
        script = generate_completion("bash", None, _OPTIONS)
        assert 'exe="opencode"' in script

    def test_happy_bash_strips_profile_or_dashdash(self) -> None:
        reg = _make_registry()
        script = generate_completion("bash", reg, _OPTIONS)
        assert '== "--"' in script
        assert 'fwd=("${fwd[@]:1}")' in script


class TestShellScriptForms:
    """Regression guards for real shell idioms that substring checks miss.

    Each assertion here failed against a plausible-but-broken generator:

    - zsh ``compadd -W "…"`` silently returns 1 (no candidates); the data
      list must be ``compadd -- 'w1' 'w2'`` so ``--flags`` are safe.
    - zsh ``${words[@]:N}`` skips *N* elements (0-based), so the native slice
      after the command is ``:1``, not ``:2``.
    - zsh must not shadow the completion context with ``words=($@)`` (``$@``
      is empty inside a completion function).
    - fish ``-w`` means "wraps another command" (not a word list); use ``-a``.
    - fish has no ``-F`` flag; a command completion is ``-a "(fn)"``.
    - the fish commandline token list is ``commandline -opc``.
    """

    def test_zsh_uses_compadd_data_list_not_dash_w(self) -> None:
        script = generate_completion("zsh", _make_registry(), _OPTIONS)
        assert "compadd -W" not in script
        assert "compadd -- " in script

    def test_zsh_no_words_clobber_and_no_trailing_call(self) -> None:
        script = generate_completion("zsh", _make_registry(), _OPTIONS)
        assert "words=($@)" not in script
        assert '_oc-selector_completions "$@"' not in script
        assert script.rstrip().endswith("}")

    def test_zsh_native_slice_skips_only_the_command(self) -> None:
        script = generate_completion("zsh", _make_registry(), _OPTIONS)
        assert 'fwd=("${words[@]:1}")' in script
        assert "${words[@]:2}" not in script

    def test_fish_uses_append_not_wraps(self) -> None:
        script = generate_completion("fish", _make_registry(), _OPTIONS)
        assert " -w " not in script
        assert " -a " in script

    def test_fish_native_uses_command_append_form(self) -> None:
        script = generate_completion("fish", _make_registry(), _OPTIONS)
        complete_lines = [
            ln for ln in script.splitlines() if ln.lstrip().startswith("complete")
        ]
        assert all(" -F " not in ln for ln in complete_lines)
        assert '-a "(__oc-selector_native)"' in script

    def test_fish_uses_opc_commandline(self) -> None:
        script = generate_completion("fish", _make_registry(), _OPTIONS)
        assert "commandline -opc" in script
        assert "commandline -xpc" not in script

    def test_fish_every_complete_line_suppresses_files(self) -> None:
        script = generate_completion("fish", _make_registry(), _OPTIONS)
        complete_lines = [
            line for line in script.splitlines() if line.lstrip().startswith("complete")
        ]
        assert complete_lines
        assert all(" -f " in line for line in complete_lines)


def _bash_branch(script: str, command: str) -> str:
    """Return the text of a single bash ``case`` branch for ``command``."""
    marker = f"{command})"
    start = script.index(marker)
    end = script.index(";;", start)
    return script[start : end + 2]
