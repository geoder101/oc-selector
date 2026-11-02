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

# The single gate: lock-check, format-check, lint, typecheck, test, build.
check: lock-check format-check lint typecheck test build

# Run coverage exploratorily, forwarding any extra arguments to pytest (no threshold).
coverage *args:
    uv run pytest --cov=oc_selector --cov-branch --cov-config=pyproject.toml --cov-report=term-missing --cov-report=html --cov-report=json {{args}}

# Run the fresh full-suite combined coverage gate at the fixed 90% threshold.
coverage-check:
    uv run python -m tools.coverage_gate
