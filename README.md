# oc-selector

A cross-platform command that selects registered OpenCode settings while keeping
normal OpenCode arguments intact.

`oc-selector` wraps the native `opencode` executable. It maintains a registry of
named settings profiles and, on each invocation, selects the registered default
(or an explicitly named profile) and forwards all remaining arguments to the
native command unchanged.

## Installation

From a local clone of the repository (run at the repo root):

```bash
uv tool install .   # recommended: isolated tool environment
# or
pip install .
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
| `oc-selector create <name> [directory] [--model MODEL] [--omo-profile OMO_PROFILE] [--no-interactive] [--dry-run]` | Create a new profile |
| `oc-selector edit <name> [--directory DIR] [--model MODEL] [--omo-profile OMO_PROFILE] [--dry-run]` | Edit an existing profile |
| `oc-selector delete <name> [--dry-run]` | Delete a profile (registry entry only) |
| `oc-selector set-default <name> [--dry-run]` | Set the default profile |
| `oc-selector list [--json]` | List all registered profiles |
| `oc-selector info [--json]` | Collect and display diagnostics |
| `oc-selector sync [PROFILE] [--all] [--dry-run]` | Sync the host package into the selected profile's directory |
| `oc-selector upgrade-omo [PROFILE] [--version VERSION] [--all] [--dry-run]` | Upgrade the OMO config to a target version |
| `oc-selector completion <shell> [--alias NAME]` | Generate shell completion |
| `oc-selector help [--version]` | Show wrapper usage and the management grammar |

### Create Options

```bash
oc-selector create <name> [directory] [--model MODEL_ID] [--omo-profile OMO_PROFILE] [--no-interactive] [--dry-run]
```

- `name`: Profile name (lowercase alphanumeric + hyphens)
- `directory`: Directory path for this profile (prompted if omitted)
- `--model, -m`: Optional model ID (e.g., `openai/gpt-4`)
- `--omo-profile`: Optional OMO profile name (e.g., `standard`)
- `--no-interactive`: Do not prompt; `directory` must be given as an argument
- `--dry-run`: Preview without writing

### Edit Options

```bash
oc-selector edit <name> [--directory DIR] [--model MODEL_ID] [--omo-profile OMO_PROFILE] [--dry-run]
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

## Documentation

The full documentation site (tutorials, how-to guides, reference, and explanation)
lives in `docs/` and is built with Sphinx. Build and preview it locally:

```bash
just docs-check       # clean build, then a strict (-W) HTML build
just docs-preview 8000  # build, then serve docs/_build/html on port 8000
```

The generated API reference under `docs/reference/api/` documents the **internal**
Python modules of the package; it is **not** a supported public API. The only
supported interface is the `oc-selector` command-line tool.

The site is deployed to GitHub Pages from the default branch; the site URL is the
deployment output URL shown by the `Documentation Pages` workflow's `deploy` job
(see [.github/workflows/README.md](.github/workflows/README.md)).

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Maintenance or argument error (native-forward and registry-load error paths exit 1 with a traceback) |
| 2 | Argument or registry error (clean, from a management command) |
| 130 | User interruption (Ctrl+C) |
| *native-forwarded* | The native `opencode` command's own exit code |

The missing-executable and non-executable native cases are **not** emitted as
distinct 127/126 codes today: they surface as exit 1 with a traceback on the
native-forward path (a known limitation). See
[docs/reference/exit-codes.rst](docs/reference/exit-codes.rst) for the full
verified table.

## Development

Development is driven by the [just](https://github.com/casey/just) task runner; the
`justfile` is the source of truth. `just check` is the single gate — run it before
considering work done. `just cli <args>` runs the tool. See `just --list` (or the
`justfile`) for the rest, including the raw command behind each recipe.

## License

MIT — see [LICENSE](LICENSE).
