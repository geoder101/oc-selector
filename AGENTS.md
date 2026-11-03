# AGENTS.md — oc-selector

Cross-platform Python 3.12 CLI that wraps the native `opencode` executable: it keeps a
registry of named settings profiles, selects the default (or a named profile), and
forwards all remaining args to `opencode` byte-for-byte.

## Commands (just-managed)

Development is driven by the [just](https://github.com/casey/just) task runner.
`just check` is the single gate: lock-check, format-check, lint, typecheck, test,
docs-check, build — in that order. Every operation has a recipe:

```bash
just sync          # verify the lockfile, then sync the dev environment
just check         # the single gate (lock-check, format-check, lint, typecheck, test, docs-check, build)
just test <args>   # tests  (single: just test tests/test_x.py::test_y)
just format        # apply the formatter
just format-check  # check formatting without changing files
just lint          # run the linter
just typecheck     # type-check with basedpyright
just cli <args>    # run the CLI, forwarding args to oc-selector
just build         # build (success message goes to STDERR, not stdout)
just docs-check    # strict (-W) Sphinx docs build (pip-only, no Node/TeX)
just docs-preview <port>  # build, then serve the docs site on <port>
just coverage <args>  # coverage run (exploratory, no threshold)
just coverage-check   # the fresh full-suite combined coverage gate (90% threshold)
just completion-e2e-image   # build the completion e2e image (one-time, network)
just completion-e2e-tests   # run the container-gated completion e2e (needs the image)
just completion-e2e         # build the image + run the e2e in one go
```

Without Just (machines that lack it), the raw equivalents are: `uv lock --check &&
uv sync --locked --group dev`, `uv run ruff format .`, `uv run ruff check .`,
`uv run basedpyright`, `uv run pytest`, and `uv build`. Run `just check` before
considering work done.

## Quality gate quirks (these will bite)

- **ruff `select = ["ALL"]` is strict**: requires docstrings (D), trailing commas
  (COM812), import sorting (I001), `from __future__ import annotations`, etc.
  `ruff check --fix` handles most; type-only imports (TC003) are manual. Every
  ignore is justified inline in `pyproject.toml` — do not add new ignores.
- **basedpyright fails on warnings too, not just errors.** A clean gate = 0 errors
  AND 0 warnings. It also **exits 3 if any `include` path is missing** — the
  `typings/` dir must exist (`.gitkeep` is enough) or the gate dies with exit 3,
  which is *not* a type error.
- **Coverage is opt-in and out of `just check`** — run `just coverage` /
  `just coverage-check` explicitly; the 90% gate is not part of the single gate.
- **The docs build is strict `-W` and pip-only** — any Sphinx warning fails
  `just docs-check`, and it needs no Node/TeX (it is part of `just check`).

## Committing

- **Commit when a logical deliverable is ready.** A commit is a self-contained
  unit that builds and passes `just check`. When such a deliverable is complete —
  a finished fix with its tests, a finished feature, a piece of tooling — commit
  it rather than letting it pile up as uncommitted work. Split a change along its
  natural seams (a code fix and the test harness that proves it are two commits);
  never commit half-finished or non-green work.
- **Conventional Commits style.** `<type>(<scope>): <imperative summary>` with
  `feat`/`fix`/`test`/`docs`/`refactor`/`build`/... and a scope naming the affected
  area (e.g. `completion`, `cli`). Keep the subject imperative and tight; add a
  short body only when the *why* is non-obvious. Never commit secrets.

## Architecture

- Entry point: `src/oc_selector/cli.py:main` (single console script `oc-selector`).
- Small, cohesive typed modules: `routing`, `registry`, `paths`, `errors`, `jsonc`,
  `omo_profiles`, `transactions`, `diagnostics`, `process`, `wizard`, `init_flow`,
  `host_sync`, `omo_upgrade`, `completion` (profile data lives in `registry`, not a
  separate module). Keep public facade imports stable; extract a same-named
  subpackage before a module exceeds ~250 pure lines.
- Each module documents its behavioral contract in its docstring — read them
  before touching routing, registry, or dispatch. Non-negotiable invariants: no
  author constants in installed `src`, no network in CLI/sync/wizard/init, no
  silent fallbacks, all mutations go through `transactions`, no migration of
  legacy fields, no conversion calls in the CLI path, idempotent operations.
- Registry lives at `XDG_CONFIG_HOME/oc-selector/config.toml`; state/backup/journal
  under `<parent>/oc-selector`. All mutations use a `portalocker` exclusive lock at
  the state root with a journal state machine (see `transactions.py`).
- OMO config stays at actual home `~/.omo`, independent of the selected OpenCode dir.

## Testing rules (critical)

- **No test may touch the real home directory or network.** Use the `conftest.py`
  fixtures: `isolated_home`, `deny_personal_home` (with a `HomeGuard.assert_isolated`
  that fails if a path escapes the sandbox), `process_harness`, and
  `fixture_executable` (fake executables that record argv/stdin/output/exit code).
  Network is isolated via proxy env vars pointed at dead `127.0.0.1:9`.
- `tests/` is an **implicit namespace package** (no `__init__.py`). `helpers.*` and
  `conftest` are imported as **absolute** imports — this works because
  basedpyright `extraPaths = ["tests"]` and pytest adds the rootdir. Do not add
  `__init__.py` to `tests/`.
- Pytest markers `real_runtime` (real pinned runtime, VM-only), `console`
  (terminal/pty), and `container` (prebuilt podman image) are declared in
  `pyproject.toml`; tests tagged with them skip cleanly when the resource is absent.
- The completion e2e lives in `tests/e2e/` (Containerfile + tmux driver) and is
  opt-in — **not part of `just check`**: `just completion-e2e-image` builds the image
  (the one network step); `just completion-e2e-tests` runs it (network-free, skips
  without podman or the image). Podman is overridable via `OC_SELECTOR_E2E_PODMAN`
  for sandboxed hosts.

## Library quirks

- `json-five` on PyPI **imports as `json5`**. `ModelLoader`/`ModelDumper` live in the
  `json5.loader` / `json5.dumper` submodules, **not** at the `json5` top level.
- `httpx2` (extras `http2,brotli,zstd`) is a real dependency, kept **lazy**: used only
  for commit-qualified native-schema downloads (bounded timeouts, redirects off,
  fixed official origin/40-hex commit path, no provider requests, no network
  schema-ref resolution).
- `.venv` is local/committed-out; `uv.lock` **is** committed and authoritative.
