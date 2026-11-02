"""Non-mutating four-shell completion script generation.

Contract:

- Support bash/zsh/fish/powershell (PowerShell >=7.3).
- Use Typer public APIs for management completion.
- Typed raw-boundary adapter for first-token profiles/management.
- Never import external Click internals.
- Generate script only on stdout, no file edits.
- Optional ``--alias NAME`` changes registration target.
- No runtime native completion subprocess or native config bootstrap.
- After native boundary, return no wrapper candidates.
- Registered profiles read safely; absent/bad registry yields safe
  management-only completion with no file changes.
"""

from __future__ import annotations

from oc_selector.errors import ArgumentError, RegistryError
from oc_selector.registry import Registry, validate_profile_name

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


def generate_completion(
    shell: str,
    registry: Registry | None,
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

    if shell == "bash":
        return _bash_script(cmd_name, profile_names)
    if shell == "zsh":
        return _zsh_script(cmd_name, profile_names)
    if shell == "fish":
        return _fish_script(cmd_name, profile_names)
    return _powershell_script(cmd_name, profile_names)


def _bash_script(cmd: str, profiles: list[str]) -> str:
    """Generate bash completion script."""
    profile_list = " ".join(profiles)
    mgmt_list = " ".join(_MANAGEMENT_COMMANDS)
    return f"""\
# {cmd} bash completion
_{cmd}_completions() {{
    local cur prev
    cur="${{COMP_WORDS[COMP_CWORD]}}"
    prev="${{COMP_WORDS[COMP_CWORD-1]}}"

    if [[ $COMP_CWORD -eq 1 ]]; then
        COMPREPLY=( $(compgen -W "{mgmt_list} {profile_list}" -- "$cur") )
        return
    fi

    case "${{COMP_WORDS[1]}}" in
        create|edit|delete|set-default|sync|upgrade-omo)
            COMPREPLY=( $(compgen -W "{profile_list}" -- "$cur") )
            ;;
        completion)
            COMPREPLY=( $(compgen -W "bash zsh fish powershell" -- "$cur") )
            ;;
    esac
    return 0
}}
complete -F _{cmd}_completions {cmd}
"""


def _zsh_script(cmd: str, profiles: list[str]) -> str:
    """Generate zsh completion script."""
    profile_list = " ".join(profiles)
    mgmt_list = " ".join(_MANAGEMENT_COMMANDS)
    return f"""\
# {cmd} zsh completion
#compdef {cmd}

_{cmd}_completions() {{
    local context state state_descr line
    words=($@)
    typeset -A opt_args

    if (( CURRENT == 2 )); then
        compadd -W "{mgmt_list} {profile_list}"
        return
    fi

    case "${{words[2]}}" in
        create|edit|delete|set-default|sync|upgrade-omo)
            compadd -W "{profile_list}"
            ;;
        completion)
            compadd -W "bash zsh fish powershell"
            ;;
    esac
}}

_{cmd}_completions "$@"
"""


def _fish_script(cmd: str, profiles: list[str]) -> str:
    """Generate fish completion script."""
    lines = [f"# {cmd} fish completion"]
    profile_list = " ".join(profiles)
    mgmt_list = " ".join(_MANAGEMENT_COMMANDS)
    lines.append(
        f"complete -c {cmd} -n '__fish_use_subcommand' -w \"{mgmt_list} {profile_list}\"",
    )
    lines.extend(
        f'complete -c {cmd} -n "__fish_seen_subcommand_from {c}" -w "{profile_list}"'
        for c in ("create", "edit", "delete", "set-default", "sync", "upgrade-omo")
    )
    lines.append(
        f'complete -c {cmd} -n "__fish_seen_subcommand_from completion" -w "bash zsh fish powershell"',
    )
    return "\n".join(lines) + "\n"


def _powershell_script(cmd: str, profiles: list[str]) -> str:
    """Generate PowerShell completion script."""
    profile_list = " ".join(profiles)
    mgmt_list = " ".join(_MANAGEMENT_COMMANDS)
    return f"""\
# {cmd} PowerShell completion
Register-ArgumentCompleter -Native -CommandName "{cmd}" {{
    param($commandName, $wordToComplete, $cursorPosition)
    $words = $wordToComplete -split ' '
    if ($words.Count -le 2) {{
        @("{mgmt_list} {profile_list}".Split(' ')) | Where-Object {{ $_ -like "$wordToComplete*" }} | ForEach-Object {{
            [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_)
        }}
    }} else {{
        switch ($words[1]) {{
            "create" {{ @("{profile_list}".Split(' ')) | Where-Object {{ $_ -like "$wordToComplete*" }} | ForEach-Object {{ [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_) }} }}
            "edit" {{ @("{profile_list}".Split(' ')) | Where-Object {{ $_ -like "$wordToComplete*" }} | ForEach-Object {{ [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_) }} }}
            "delete" {{ @("{profile_list}".Split(' ')) | Where-Object {{ $_ -like "$wordToComplete*" }} | ForEach-Object {{ [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_) }} }}
            "set-default" {{ @("{profile_list}".Split(' ')) | Where-Object {{ $_ -like "$wordToComplete*" }} | ForEach-Object {{ [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_) }} }}
            "completion" {{ @("bash","zsh","fish","powershell") | Where-Object {{ $_ -like "$wordToComplete*" }} | ForEach-Object {{ [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_) }} }}
        }}
    }}
}}
"""
