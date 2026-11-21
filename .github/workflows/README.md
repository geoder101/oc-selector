# CI workflows

This directory holds the three GitHub Actions workflows for `oc-selector`.
This file is the map of what each workflow does; the YAML files are the truth.

## Shared setup action

The composite action at [`.github/actions/setup`](.github/actions/setup/action.yml)
now installs **only** the pinned toolchain (`setup-uv`, `setup-just`), declared and
bumped in one place. It no longer checks out the code. Each consumer performs its
**own** inline `actions/checkout@v7.0.1` at the event SHA first, then calls the
composite with `uses: ./.github/actions/setup`. That split keeps the checkout pinned
to an immutable SHA (enforced by `tests/test_ci_workflows.py`) while still sharing a
single toolchain definition.

Call it with `uses: ./.github/actions/setup`; pass `just-version: "1.59.0"` where
`just` is needed.

## Job timeouts and artifact warnings

Every job carries a `timeout-minutes` ceiling, and every upload uses
`if-no-files-found: warn` so a missing artifact surfaces as a warning rather
than a silent pass.

## `ci.yml` — CI

**Triggers:** `pull_request` and `push` (any branch).

| Job | Runs on | What it proves |
|-----|---------|----------------|
| `test` — "Check (${{ matrix.os }})" | `ubuntu-latest`, `macos-latest`, `windows-latest` (matrix, `fail-fast: false`) | The single gate `just check` (lock-check, format-check, lint, typecheck, test, docs-check, build) passes on all three operating systems. This is also where the docs build is proven on macOS and Windows. |
| `coverage` — "Coverage evidence" | `ubuntu-latest` | The fresh full-suite combined coverage gate (`just coverage-check`, fixed 90% threshold) passes. |

**Artifact:** `coverage-${{ github.run_id }}-${{ github.run_attempt }}`, uploaded with
`if: always()` so it exists even when the gate fails. Contents: `coverage/html/`
(the HTML report), `coverage/coverage.json` (machine-readable report), and
`coverage/.coverage` (the raw data file). **Retention: 7 days.**

## `pages.yml` — Documentation Pages

**Triggers:** `push` (any branch) and `workflow_dispatch`.

| Job | Runs on | What it proves |
|-----|---------|----------------|
| `authorize` | `ubuntu-latest` | The event is authorized: only `push` and `workflow_dispatch` events, only the repository's actual default branch (`refs/heads/<default_branch>`), and only a 40-character lowercase hex SHA. Emits the authorized SHA as a job output. |
| `build` | `ubuntu-latest` | Checks out exactly the authorized SHA (`persist-credentials: false`), verifies `HEAD` matches and the working tree is clean, then proves the strict docs build (`just docs-check`, `-W`) passes. Uploads `docs/_build/html` as the `github-pages` artifact via `upload-pages-artifact`. |
| `deploy` | `ubuntu-latest` | Reads the `github-pages` artifact by name and deploys it via the `deploy-pages` action in the `github-pages` environment (no separate download or configure step). The site URL is the **deployment output** (`steps.deployment.outputs.page_url`), surfaced on the environment — it is not hardcoded anywhere. |

**Concurrency:** group `github-pages`, `cancel-in-progress: false` — deployments
queue rather than cancel each other.

### Pages authorization model

- **Default-branch-only.** The `authorize` job compares the event ref against
  `refs/heads/${{ github.event.repository.default_branch }}` and fails the run
  for anything else.
- **PRs never deploy.** `pull_request` is not an authorized event name; the
  `authorize` job exits 1 for it. Pull requests run `ci.yml` only.
- **The URL comes from deployment output.** The `deploy` job's
  `environment.url` is `steps.deployment.outputs.page_url`, so the canonical
  site URL is whatever GitHub Pages reports for the deployment — never an
  invented or hardcoded value.
- **Environment gate.** `deploy` runs in the `github-pages` environment, which
  the repository owner restricts to the default branch (see the setup checklist
  below). The job needs `pages: write` and `id-token: write`; the workflow-level
  permission is `contents: read`.

## `release.yml` — Release

**Triggers:** `push` to `main` only. Workflow-level permission: `contents: write`.

| Job | Runs on | What it proves |
|-----|---------|----------------|
| `release-test` | `ubuntu-latest` | The single gate `just check` passes on the pushed commit before anything is built or published. |
| `release-build` | `ubuntu-latest` | `uv build` produces the sdist and wheel; uploads them as the `dist` artifact. |
| `publish` | `ubuntu-latest` | Runs only on `push` to `refs/heads/main`, in the `release` environment. Creates (or updates, with `--clobber`) the GitHub Release tagged `v<version>` from the wheel filename. |

## Local smoke test with `act`

Testing here is a two-layer strategy. The static policy tests in
`tests/test_ci_workflows.py` enforce the invariants that `act` cannot check: every
hosted action is pinned to an immutable commit SHA with a matching release-tag
comment, and no forbidden token leaks into a workflow. Those two tests are the
authority on pinning. `act` is a complementary tool, it renders the workflows and
lets you iterate locally before pushing.

The verified commands, run from the repo root:

- `gh act --version`: confirm `act` is installed.
- `gh act -l`: enumerate every job across all three workflows. This is the local
  gate. It must show each job once (no duplicate job names), which is how the
  `release-test` / `release-build` disambiguation in the release workflow is
  verified.
- `gh act -n -W .github/workflows/ci.yml -P default=catthehacker/ubuntu:22.04`:
  dry-run the CI workflow against a container image without touching GitHub.

Container socket. `act` runs each job in a container, so the dry-run needs a
reachable docker/podman socket. Point it at one with `DOCKER_HOST`, for example:

```bash
export DOCKER_HOST=unix://run/podman.sock
gh act -n -W .github/workflows/ci.yml -P default=catthehacker/ubuntu:22.04
```

Sandbox caveat. Run this dry-run locally, not in hosted CI, and it needs the socket
above. In a socket-less sandbox the dry-run cannot run, so `gh act -l` plus the
static policy tests are the local gate. The docker-connection warning `gh act -l`
prints in that case is environmental, not a workflow problem.

Maintenance note. When an action version is bumped, update its entry in the
`EXPECTED_SHA/EXPECTED_TAG` tables in `tests/test_ci_workflows.py` alongside the
workflow, or the SHA-pin test fails.

## One-time owner setup (GitHub)

Do these once, before the first hosted run:

1. **Create and push the repository.** Push the default branch (`main`) to
   GitHub.
2. **Point Pages at Actions.** Repository **Settings → Pages → Source** =
   **GitHub Actions**.
3. **Create the `github-pages` environment.** **Settings → Environments → New
   environment** named `github-pages`, with a branch restriction to the default
   branch (`main`). This is the second authorization layer behind the
   `authorize` job.
4. **Create the `release` environment** (recommended: add a required reviewer).
5. **First-run verification.** Push to `main` and confirm:
   - `CI` is green on all three OS legs plus the coverage job;
   - `Documentation Pages` is green through `authorize` → `build` → `deploy`,
     and the `deploy` job's environment shows the site URL (the deployment
     output URL);
    - `Release` created the GitHub Release.

## Honest status

These workflows are **unverified on GitHub until the repository is actually
pushed there.** Everything above is derived from the committed YAML. In
particular, the 3-OS docs build (the `docs-check` step inside `just check` on
the `ci.yml` matrix) is first verified on the **first hosted run** — local
proof of the docs build is Linux-only.
