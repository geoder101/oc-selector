"""Non-mutating four-shell completion script generation.

Contract:

- Support bash/zsh/fish/powershell (PowerShell >=7.3).
- Use Typer public APIs for management completion.
- Typed raw-boundary adapter for first-token profiles/management.
- Never import external Click internals.
- Generate script only on stdout, no file edits.
- Optional ``--alias NAME`` changes registration target.
- No Python-side native subprocess or native config bootstrap; the generated
  scripts may invoke the resolved opencode executable at completion time.
- After the native boundary, delegate to opencode's Yargs completion
  (``--get-yargs-completions``) instead of offering wrapper candidates.
- Management subcommands complete their declared Typer option switches,
  derived from the live command tree (no hardcoded flag lists); the Typer
  ``--help`` option is always offered as well.
- Registered profiles read safely; absent/bad registry yields safe
  management-only completion with no file changes.
"""

from __future__ import annotations

import typing
from typing import TYPE_CHECKING

from typer import main as _typer_main
from typer.models import OptionInfo

from oc_selector.errors import ArgumentError, RegistryError
from oc_selector.registry import Registry, validate_profile_name

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from typer import Typer
    from typer.models import CommandInfo


_SUPPORTED_SHELLS: frozenset[str] = frozenset({"bash", "zsh", "fish", "powershell"})

_MANAGEMENT_COMMANDS: tuple[str, ...] = (
    "list",
    "init",
    "create",
    "edit",
    "delete",
    "set-default",
    "sync",
    "upgrade-omo",
    "completion",
    "help",
    "info",
)

# Commands whose first positional argument is a profile name.
_PROFILE_COMMANDS: frozenset[str] = frozenset(
    {"create", "edit", "delete", "set-default", "sync", "upgrade-omo"},
)

_SHELL_CHOICES: tuple[str, ...] = ("bash", "zsh", "fish", "powershell")

# opencode's Yargs completion emits a "$0" placeholder for its default (TUI)
# command; it is noise and is filtered out of every delegated candidate list.
_NATIVE_FILTER = "grep -v -x -F -- '$0'"


def validate_shell(shell: str) -> str:
    """Validate shell name. Raises ``ArgumentError`` if unsupported."""
    if shell not in _SUPPORTED_SHELLS:
        raise ArgumentError(
            f"Unsupported shell: {shell!r}. Supported: {', '.join(sorted(_SUPPORTED_SHELLS))}",
        )
    return shell


def validate_alias(alias: str) -> str:
    """Validate alias name (same rules as profile names)."""
    try:
        validate_profile_name(alias)
    except RegistryError as e:
        raise ArgumentError(str(e)) from e
    return alias


def extract_command_options(app: Typer) -> dict[str, list[str]]:
    """Derive declared option switches for each registered management command.

    Reads the Typer command tree only (no file I/O, subprocess, or network).
    Positional arguments are excluded; only option flags (long and short forms)
    are returned, in declaration order, keyed by command name.
    """
    options: dict[str, list[str]] = {}
    for info in app.registered_commands:
        name = _command_name(info)
        if not name:
            continue
        callback = info.callback
        if callback is None:
            continue
        options[name] = _option_flags(callback)
    return options


def _command_name(info: CommandInfo) -> str:
    """Return the command name Typer registers for a command."""
    if info.name is not None:
        return info.name
    callback = info.callback
    if callback is None:
        return ""
    return _typer_main.get_command_name(callback.__name__)


def _option_flags(callback: Callable[..., object]) -> list[str]:
    """Return the option flag names declared on a command callback.

    Only ``typer.Option`` parameters contribute; ``typer.Argument`` parameters
    (positionals) are ignored. The primary and any secondary flag forms are
    included, in declaration order.
    """
    flags: list[str] = []
    hints: dict[str, object] = typing.get_type_hints(callback, include_extras=True)
    for annotation in hints.values():
        if typing.get_origin(annotation) is not typing.Annotated:
            continue
        args: tuple[object, ...] = typing.get_args(annotation)
        for meta in args[1:]:
            if not isinstance(meta, OptionInfo):
                continue
            primary = meta.default
            if isinstance(primary, str):
                flags.append(primary)
            flags.extend(meta.param_decls or ())
    return flags


def _command_words(
    command: str,
    profiles: Sequence[str],
    command_options: Mapping[str, Sequence[str]],
) -> list[str]:
    """Return the completion words for a command (positionals, then switches).

    Typer injects a ``--help`` option into every command, so it is always
    offered in addition to the command's declared switches; a command with no
    declared switches still yields a branch (``--help`` alone).
    """
    words: list[str] = []
    if command in _PROFILE_COMMANDS:
        words.extend(profiles)
    elif command == "completion":
        words.extend(_SHELL_CHOICES)
    words.extend(command_options.get(command, ()))
    words.append("--help")
    return words


def generate_completion(
    shell: str,
    registry: Registry | None,
    command_options: Mapping[str, Sequence[str]],
    alias: str | None = None,
) -> str:
    """Generate a completion script for the given shell.

    Returns the script text. No file I/O, no subprocess, no network.
    """
    validate_shell(shell)
    if alias is not None:
        validate_alias(alias)

    profile_names: list[str] = []
    if registry is not None:
        profile_names = sorted(registry.profiles.keys())

    cmd_name = alias or "oc-selector"
    exe = registry.opencode_executable if registry is not None else "opencode"

    if shell == "bash":
        return _bash_script(cmd_name, profile_names, command_options, exe)
    if shell == "zsh":
        return _zsh_script(cmd_name, profile_names, command_options, exe)
    if shell == "fish":
        return _fish_script(cmd_name, profile_names, command_options, exe)
    return _powershell_script(cmd_name, profile_names, command_options, exe)


def _bash_script(
    cmd: str,
    profiles: Sequence[str],
    command_options: Mapping[str, Sequence[str]],
    exe: str,
) -> str:
    """Generate bash completion script."""
    profile_list = " ".join(profiles)
    mgmt_list = " ".join(_MANAGEMENT_COMMANDS)
    branches: list[str] = []
    for command in _MANAGEMENT_COMMANDS:
        words = _command_words(command, profiles, command_options)
        if not words:
            continue
        branches.append(
            "\n".join(
                [
                    f"        {command})",
                    f'            COMPREPLY=( $(compgen -W "{_join(words)}" -- "$cur") ); return 0 ;;',
                ],
            ),
        )
    branch_text = "\n".join(branches)
    return f"""\
# {cmd} bash completion
_{cmd}_completions() {{
    local cur words prev exe
    cur="${{COMP_WORDS[COMP_CWORD]}}"
    words=("${{COMP_WORDS[@]:1}}")
    prev=("${{words[@]:0:${{#words[@]}}-1}}")
    exe="{exe}"

    if [[ ${{#prev[@]}} -eq 0 ]]; then
        local native
        native=$( "$exe" --get-yargs-completions "$cur" | {_NATIVE_FILTER} )
        COMPREPLY=( $(compgen -W "{mgmt_list} {profile_list} $native" -- "$cur") )
        return 0
    fi

    case "${{prev[0]}}" in
{branch_text}
        *)
            local fwd native
            fwd=("${{words[@]}}")
            if [[ "${{fwd[0]}}" == "--" || " {profile_list} " == *" ${{fwd[0]}} "* ]]; then
                fwd=("${{fwd[@]:1}}")
            fi
            native=$( "$exe" --get-yargs-completions "${{fwd[@]}}" | {_NATIVE_FILTER} )
            COMPREPLY=( $(compgen -W "$native" -- "$cur") )
            ;;
    esac
    return 0
}}
complete -F _{cmd}_completions {cmd}
"""


def _zsh_script(
    cmd: str,
    profiles: Sequence[str],
    command_options: Mapping[str, Sequence[str]],
    exe: str,
) -> str:
    """Generate zsh completion script."""
    profile_list = " ".join(profiles)
    first_token = _zsh_quoted(list(_MANAGEMENT_COMMANDS) + list(profiles))
    branches: list[str] = []
    for command in _MANAGEMENT_COMMANDS:
        words = _command_words(command, profiles, command_options)
        if not words:
            continue
        branches.append(
            "\n".join(
                [
                    f"        {command})",
                    f"            compadd -- {_zsh_quoted(words)}",
                    "            ;;",
                ],
            ),
        )
    branch_text = "\n".join(branches)
    return f"""\
# {cmd} zsh completion
#compdef {cmd}

_{cmd}_completions() {{
    local cur
    cur="${{words[CURRENT]}}"
    local exe="{exe}"

    if (( CURRENT == 2 )); then
        local native
        native=$( "$exe" --get-yargs-completions "$cur" | {_NATIVE_FILTER} )
        compadd -- {first_token} ${{=native}}
        return
    fi

    case "${{words[2]}}" in
{branch_text}
        *)
            local fwd native
            fwd=("${{words[@]:1}}")
            if [[ ${{#fwd[@]}} -gt 0 ]]; then
                local f0="${{fwd[1]}}"
                if [[ "$f0" == "--" || " {profile_list} " == *" $f0 "* ]]; then
                    fwd=("${{fwd[@]:1}}")
                fi
            fi
            native=$( "$exe" --get-yargs-completions "${{fwd[@]}}" | {_NATIVE_FILTER} )
            compadd -- ${{=native}}
            ;;
    esac
}}
"""


def _fish_script(
    cmd: str,
    profiles: Sequence[str],
    command_options: Mapping[str, Sequence[str]],
    exe: str,
) -> str:
    """Generate fish completion script."""
    profile_list = " ".join(profiles)
    mgmt_list = " ".join(_MANAGEMENT_COMMANDS)
    lines: list[str] = [f"# {cmd} fish completion"]
    lines.append(
        f"complete -c {cmd} -f -n '__fish_use_subcommand' -a \"{mgmt_list} {profile_list}\"",
    )
    for command in _MANAGEMENT_COMMANDS:
        words = _command_words(command, profiles, command_options)
        if not words:
            continue
        lines.append(
            f'complete -c {cmd} -f -n "__fish_seen_subcommand_from {command}" -a "{_join(words)}"',
        )
    lines.append(f"function __{cmd}_native")
    lines.append("    set -l toks (commandline -opc)")
    lines.append(f'    set -l exe "{exe}"')
    lines.append(f"    set -l mgmt (string split ' ' -- \"{mgmt_list}\")")
    lines.append(f"    set -l profiles (string split ' ' -- \"{profile_list}\")")
    lines.append("    if test (count $toks) -ge 2")
    lines.append("        if contains -- $toks[2] $mgmt")
    lines.append("            return")
    lines.append("        end")
    lines.append("    end")
    lines.append("    set -l fwd $toks[2..-1]")
    lines.append("    if test (count $fwd) -gt 0")
    lines.append('        if test $fwd[1] = "--"')
    lines.append("            set fwd $fwd[2..-1]")
    lines.append("        else if contains -- $fwd[1] $profiles")
    lines.append("            set fwd $fwd[2..-1]")
    lines.append("        end")
    lines.append("    end")
    lines.append(f"    $exe --get-yargs-completions $fwd | {_NATIVE_FILTER}")
    lines.append("end")
    lines.append(f'complete -c {cmd} -f -a "(__{cmd}_native)"')
    return "\n".join(lines) + "\n"


def _powershell_script(
    cmd: str,
    profiles: Sequence[str],
    command_options: Mapping[str, Sequence[str]],
    exe: str,
) -> str:
    """Generate PowerShell completion script."""
    mgmt_array = ",".join(f'"{name}"' for name in _MANAGEMENT_COMMANDS)
    profile_array = ",".join(f'"{name}"' for name in profiles)
    branch_lines: list[str] = []
    for command in _MANAGEMENT_COMMANDS:
        words = _command_words(command, profiles, command_options)
        if not words:
            continue
        word_list = _join(words).replace("'", "''")
        branch_lines.append(
            f'            "{command}" {{ & $emit (@("{word_list}".Split(\' \'))) }}',
        )
    branch_text = "\n".join(branch_lines)
    return f"""\
# {cmd} PowerShell completion
Register-ArgumentCompleter -Native -CommandName "{cmd}" {{
    param($commandName, $wordToComplete, $cursorPosition)
    $exe = "{exe}"
    $mgmt = @({mgmt_array})
    $profiles = @({profile_array})
    $words = @($wordToComplete -split ' ')
    $cur = $words[$words.Count - 1]
    $emit = {{
        param($candidates)
        $candidates | Where-Object {{ $_ -like "$cur*" }} | ForEach-Object {{
            [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_)
        }}
    }}
    # When only the current word is present (PowerShell native completers pass
    # the current word), offer management + profiles + native top-level.
    if ($words.Count -le 1) {{
        $native = & $exe --get-yargs-completions $cur | Where-Object {{ $_ -ne '$0' }}
        & $emit (@($mgmt) + @($profiles) + @($native))
        return
    }}
    switch ($words[1]) {{
{branch_text}
        default {{
            $fwd = @($words[1..($words.Count - 1)])
            if ($fwd.Count -gt 0 -and ($fwd[0] -eq "--" -or $profiles -contains $fwd[0])) {{
                $fwd = @($fwd[1..($fwd.Count - 1)])
            }}
            $native = & $exe --get-yargs-completions @($fwd) | Where-Object {{ $_ -ne '$0' }}
            & $emit (@($native))
        }}
    }}
}}
"""


def _join(words: Sequence[str]) -> str:
    """Space-join completion words for embedding in a shell word list."""
    return " ".join(words)


def _zsh_quoted(words: Sequence[str]) -> str:
    """Quote each word for a zsh ``compadd --`` data list (safe for ``--flags``)."""
    return " ".join(f"'{word}'" for word in words)
