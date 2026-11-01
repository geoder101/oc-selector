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
| `oc-selector init` | Initialize configuration and registry |
| `oc-selector create <name> <dir>` | Create a new profile |
| `oc-selector edit <name>` | Edit an existing profile |
| `oc-selector set-default <name>` | Set the default profile |
| `oc-selector list` | List all registered profiles |
| `oc-selector diagnostics` | Collect and display diagnostics |
| `oc-selector completion <shell>` | Generate shell completion |
| `oc-selector recover` | Recover from pending transactions |

### Create Options

```bash
oc-selector create <name> <directory> [--model MODEL_ID]
```

- `name`: Profile name (lowercase alphanumeric + hyphens)
- `directory`: Directory path for this profile
- `--model, -m`: Optional model ID (e.g., `openai/gpt-4`)

### Edit Options

```bash
oc-selector edit <name> [--directory DIR] [--model MODEL_ID]
```

Only specified fields are changed; others are preserved.

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

```bash
# Setup
uv sync

# Run tests
uv run pytest

# Lint
uv run ruff check .

# Type check
uv run basedpyright

# Build
uv build
```

## License

MIT — see [LICENSE](LICENSE).
