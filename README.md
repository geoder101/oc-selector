# oc-selector

A cross-platform command that selects registered OpenCode settings while keeping
normal OpenCode arguments intact.

`oc-selector` wraps the native `opencode` executable. It maintains a registry of
named settings profiles and, on each invocation, selects the registered default
(or an explicitly named profile) and forwards all remaining arguments to the
native command unchanged.

## Installation

```bash
pip install oc-selector
# or
uv tool install oc-selector
```

## Quick Start

```bash
# Initialize configuration
oc-selector init

# Create a profile
oc-selector create work work-dir --model openai/gpt-4

# Create another profile
oc-selector create personal personal-dir

# Set the default profile
oc-selector set-default work

# List all profiles
oc-selector list

# Use it - all args forward to native opencode
oc-selector --model openai/gpt-4 "fix the bug"
```

## Commands

| Command | Description |
|---------|-------------|
| `oc-selector init [--no-backup] [--dry-run] [--recover]` | Initialize configuration and registry, or recover pending transactions |
| `oc-selector create <name> [directory] [--model MODEL] [--no-interactive] [--dry-run]` | Create a new profile |
| `oc-selector edit <name> [--directory DIR] [--model MODEL] [--dry-run]` | Edit an existing profile |
| `oc-selector set-default <name> [--dry-run]` | Set the default profile |
| `oc-selector list [--json]` | List all registered profiles |
| `oc-selector info [--json]` | Collect and display diagnostics |
| `oc-selector sync [PROFILE] [--all] [--dry-run]` | Sync the host package into the selected profile's directory |
| `oc-selector upgrade-omo [PROFILE] [--version VERSION] [--all] [--dry-run]` | Upgrade the OMO config to a target version |
| `oc-selector completion <shell> [--alias NAME]` | Generate shell completion |
| `oc-selector help [--version]` | Show wrapper usage and the management grammar |

### Create Options

```bash
oc-selector create <name> [directory] [--model MODEL_ID] [--no-interactive] [--dry-run]
```

- `name`: Profile name (lowercase alphanumeric + hyphens)
- `directory`: Directory path for this profile (prompted if omitted)
- `--model, -m`: Optional model ID (e.g., `openai/gpt-4`)
- `--no-interactive`: Do not prompt; `directory` must be given as an argument
- `--dry-run`: Preview without writing

### Edit Options

```bash
oc-selector edit <name> [--directory DIR] [--model MODEL_ID] [--dry-run]
```

Only specified fields are changed; others are preserved. `--dry-run` previews without writing.

## Shell Completion

```bash
# Bash
eval "$(oc-selector completion bash)"

# Zsh
eval "$(oc-selector completion zsh)"

# Fish
oc-selector completion fish | source

# PowerShell
oc-selector completion powershell | Out-String | Invoke-Expression
```

## Configuration

Registry is stored at `$XDG_CONFIG_HOME/oc-selector/config.toml`
(default: `~/.config/oc-selector/config.toml`).

```toml
schema_version = 1
default_profile = "work"
opencode_executable = "opencode"

[profiles.work]
directory = "work-dir"

[profiles.personal]
directory = "personal-dir"
```

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Maintenance error (lock, transaction) |
| 2 | Argument or registry error |
| 127 | Native executable not found |
| 126 | Native executable not executable |
| 130 | User interruption (Ctrl+C) |

## Development

Development is driven by the [just](https://github.com/casey/just) task runner.
`just check` is the single gate: it runs lock-check, format-check, lint, typecheck,
test, and build in order. Every operation has a recipe:

```bash
just sync          # verify the lockfile, then sync the dev environment
just check         # the single gate (lock-check, format-check, lint, typecheck, test, build)
just test          # run the test suite (forward extra args to pytest)
just format        # apply the formatter
just format-check  # check formatting without changing files
just lint          # run the linter
just typecheck     # type-check with basedpyright
just cli <args>    # run the CLI, forwarding args to oc-selector
just build         # build the sdist and wheel
```

Without Just (machines that lack it), the equivalent raw commands are:
`uv lock --check && uv sync --locked --group dev`, `uv run ruff format .`,
`uv run ruff check .`, `uv run basedpyright`, `uv run pytest`, and `uv build`.

## License

MIT — see [LICENSE](LICENSE).
