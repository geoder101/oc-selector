# oc-selector

A cross-platform command that selects registered OpenCode settings while keeping
normal OpenCode arguments intact.

`oc-selector` wraps the native `opencode` executable. It maintains a registry of
named settings profiles and, on each invocation, selects the registered default
(or an explicitly named profile) and forwards all remaining arguments to the
native command unchanged.

## Status

This package is under active development. The public command surface is being
built incrementally; see the project plan for the full lifecycle.

## License

MIT — see [LICENSE](LICENSE).
