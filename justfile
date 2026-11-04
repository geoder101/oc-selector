# Task runner for the oc-selector development workflow.
# `just check` is the single documented gate; every dev operation has a recipe.

[unix]
set shell := ["sh", "-cu"]

[windows]
set shell := ["powershell.exe", "-NoLogo", "-Command"]

# Pin coverage data under coverage/ for the coverage recipes (inert elsewhere).
export COVERAGE_FILE := "coverage/.coverage"

# Show this list of recipes (the default action).
default:
    just --list

# Verify the lockfile is up to date before syncing.
lock-check:
    uv lock --check

# Sync the locked dev environment after verifying the lockfile.
sync: lock-check
    uv sync --locked --group dev

# Upgrade all packages to the latest compatible versions (refreshes index metadata), sync, then run `just check`.
upgrade-deps: lock-check
    uv lock --upgrade --refresh
    uv sync --locked --group dev

# Check formatting without changing files.
format-check:
    uv run ruff format --check .

# Apply the project formatter to all source files.
format:
    uv run ruff format .

# Run the linter over the whole repository.
lint:
    uv run ruff check .

# Type-check with basedpyright (fails on warnings too).
typecheck:
    uv run basedpyright

# Run the test suite, forwarding any extra arguments to pytest.
test *args:
    uv run pytest {{args}}

# Run the CLI, forwarding any extra arguments to oc-selector.
cli *args:
    uv run oc-selector {{args}}

# Build the sdist and wheel (success message goes to stderr).
build:
    uv build

# Install the tool locally into a managed uv tool environment (exposes `oc-selector` on PATH).
install:
    uv tool install .

# The single gate: lock-check, format-check, lint, typecheck, test, docs-check, build.
check: lock-check format-check lint typecheck test docs-check build

# Run coverage exploratorily, forwarding any extra arguments to pytest (no threshold).
coverage *args:
    uv run pytest --cov=oc_selector --cov-branch --cov-config=pyproject.toml --cov-report=term-missing --cov-report=html --cov-report=json {{args}}

# Run the fresh full-suite combined coverage gate at the fixed 90% threshold.
coverage-check:
    uv run python -m tools.coverage_gate

# Build the docs site strictly: a clean build, then a -W HTML build that fails on any warning.
docs-check:
    uv run --group docs sphinx-build -M clean docs docs/_build
    uv run --group docs sphinx-build -M html docs docs/_build -E -a -W

# Build the docs site, then serve the HTML output on the given port (default 8000).
docs-preview port="8000": docs-check
    python -m http.server --directory docs/_build/html {{port}}

# Build the completion e2e container image (one-time, opt-in network step).
completion-e2e-image:
    podman build -t oc-selector-completion-e2e:latest tests/e2e

# Run the container-gated completion e2e tests (needs the image already built).
completion-e2e-tests:
    uv run pytest -m container tests/test_completion_e2e.py -v

# Build the image and run the completion e2e in one go.
completion-e2e: completion-e2e-image completion-e2e-tests
