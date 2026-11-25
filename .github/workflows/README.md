# CI workflows

This directory holds the three GitHub Actions workflows for `oc-selector`.
This file is the map of what each workflow does; the YAML files are the truth.

## Shared setup action

The composite action at [`.github/actions/setup`](.github/actions/setup/action.yml)
installs the pinned toolchain (`setup-uv`, `setup-just`) and keeps it declared and
bumped in one place. Each consumer performs its **own** `actions/checkout@v7.0.1` at
the event SHA, then calls the composite with `uses: ./.github/actions/setup`. Keeping
checkout inline in the consumers while sharing one toolchain definition keeps the
checkout pinned to an immutable SHA (enforced by `tests/test_ci_workflows.py`) and the
toolchain versioned in a single place.

Call it with `uses: ./.github/actions/setup`; pass `just-version: "1.59.0"` where
`just` is needed (it is the default, so most consumers omit it).

## Job timeouts and artifact warnings

Every job carries a `timeout-minutes` ceiling, and every upload uses
`if-no-files-found: warn` so a missing artifact surfaces as a warning rather
than a silent pass.

## `ci.yml` — CI

**Triggers:** `pull_request`, plus `workflow_call` when `main` is pushed to (via the Release workflow).

| Job | Runs on | What it proves |
|-----|---------|----------------|
| `check` | `ubuntu-latest`, `macos-latest`, `windows-latest` | The single gate `just check` (lock-check, format-check, lint, typecheck, test, docs-check, build) passes on all three platforms. Matrix failures do not cancel the remaining legs. |
| `coverage` | `ubuntu-latest` | The fresh full-suite combined coverage gate (`just coverage-check`, fixed 90% threshold) passes. |

**Artifact:** `coverage-${{ github.run_id }}-${{ github.run_attempt }}`, uploaded with
`if: always()` so it exists even when the gate fails. Contents: `coverage/html/`
(the HTML report), `coverage/coverage.json` (machine-readable report), and
`coverage/.coverage` (the raw data file). **Retention: 7 days.**

## `pages.yml` — Documentation Pages

**Triggers:** `push` to `main`.

| Job | Runs on | What it proves |
|-----|---------|----------------|
| `authorize` | `ubuntu-latest` | Confirms the event is a push to `main` and that `main` is the repository's default branch before building or deploying. |
| `build` | `ubuntu-latest` | Checks out the event SHA (`persist-credentials: false`), then proves the strict docs build (`just docs-check`, `-W`) passes. Uploads `docs/_build/html` as the `github-pages` artifact via `upload-pages-artifact`. |
| `deploy` | `ubuntu-latest` | Reads the `github-pages` artifact by name and deploys it via the `deploy-pages` action in the `github-pages` environment (no separate download or configure step). The site URL is the **deployment output** (`steps.deployment.outputs.page_url`), surfaced on the environment — it is not hardcoded anywhere. |

**Concurrency:** group `github-pages`, `cancel-in-progress: false` — deployments
queue rather than cancel each other.

### Pages authorization model

- **Default-branch-only.** The trigger fires on pushes to `main` only, so the
  build and deploy run only on that push; pull requests and non-default
  branches build and deploy nothing.
- **PRs never deploy.** `pages.yml` does not trigger on `pull_request` (the
  trigger is `main`-only), so pull requests build and deploy nothing; they are
  checked by `ci.yml` only.
- **The URL comes from deployment output.** The `deploy` job's
  `environment.url` is `steps.deployment.outputs.page_url`, so the canonical
  site URL is whatever GitHub Pages reports for the deployment — never an
  invented or hardcoded value.
- **Environment gate.** `deploy` runs in the `github-pages` environment, which
  the repository owner restricts to the default branch. The job needs
  `pages: write` and `id-token: write`; the workflow-level permission is
  `contents: read`.

## `release.yml` — Release

**Triggers:** `push` to `main` only. Workflow-level permission: `contents: read`.

| Job | Runs on | What it proves |
|-----|---------|----------------|
| `test` | Reusable CI | Runs the full CI matrix (`check` on all three OS plus the 90% coverage gate) via `ci.yml`, gating the build before anything is published. |
| `publish` | `ubuntu-latest` | Runs only on `push` to `refs/heads/main`, in the `release` environment. Builds the distribution with `just build`, then creates (or updates, with `--clobber`) the GitHub Release tagged `v<version>` from `pyproject.toml` using the repository's built-in token. New tags target the tested event SHA. |

Only the `publish` job receives `contents: write` (required to open the release);
the `test` job and all other workflows stay read-only. The GitHub Release is gated
on the default branch and the `release` environment; the `release` environment can
additionally require a reviewer.

## Local smoke test with `act`

Testing here is a two-layer strategy. The static policy tests in
`tests/test_ci_workflows.py` enforce the invariants that `act` cannot check: every
hosted action is pinned to an immutable commit SHA with a matching release-tag
comment, and no forbidden token leaks into a workflow. Those two tests are the
authority on pinning. `act` ([nektos/act](https://github.com/nektos/act)) is a
complementary tool, it renders the workflows and lets you iterate locally before
pushing.

The verified commands, run from the repo root:

- `gh act --version`: confirm `act` is installed.
- `gh act -l`: enumerate every job across all three workflows. This is the local
  gate. It must show each job once (no duplicate job names).
- `gh act -n -W .github/workflows/ci.yml -P default=catthehacker/ubuntu:22.04`:
  dry-run the CI workflow against a container image without touching GitHub.

Container socket. `act` runs each job in a container, so the dry-run needs a
reachable docker/podman socket. Point it at one with `DOCKER_HOST`, for example:

```bash
export DOCKER_HOST=unix://run/podman.sock
gh act -n -W .github/workflows/ci.yml -P default=catthehacker/ubuntu:22.04
```

## Keeping action pins in sync

Every hosted action is pinned to an immutable commit SHA. When you bump an action
version, update its entry in the `EXPECTED_SHA/EXPECTED_TAG` tables in
`tests/test_ci_workflows.py` alongside the workflow, or the SHA-pin test fails.
