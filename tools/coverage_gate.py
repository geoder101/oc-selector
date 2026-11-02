"""Fresh full-suite combined coverage gate for oc-selector.

Usage::

    uv run python -m tools.coverage_gate

The gate takes no arguments. It clears stale coverage outputs, runs the FULL
default test suite under ``coverage run`` (branch coverage enabled via the
``[tool.coverage.*]`` config in ``pyproject.toml``), generates the JSON and
HTML reports under ``coverage/``, and computes the COMBINED statement+branch
percentage as the exact fraction ``covered_units / total_units`` where units
are the raw statement and branch counts from the coverage data (never the
rounded terminal percentage). It exits non-zero if the suite failed, if no
coverage data was produced, or if the combined percentage is below
``REQUIRED_COMBINED``; on success it prints the exact combined percentage and
a per-file table and exits zero. Raw coverage data always lives under
``coverage/`` (``COVERAGE_FILE=coverage/.coverage``), never at the repo root.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from pydantic import BaseModel

# The gate threshold: the combined statement+branch percentage must be at
# least this value. The percentage is the EXACT fraction
# (covered statements + covered branches) / (total statements + total
# branches) computed from the raw coverage counts, compared as a float —
# never the rounded percentage printed in a terminal report.
REQUIRED_COMBINED = 90.0

# All coverage outputs live under coverage/ so raw data never lands at the
# repo root; the pyproject [tool.coverage.json]/[tool.coverage.html] config
# writes the reports to the same directory.
_COVERAGE_DIR = Path("coverage")
_DATA_FILE = _COVERAGE_DIR / ".coverage"
_JSON_FILE = _COVERAGE_DIR / "coverage.json"
_HTML_DIR = _COVERAGE_DIR / "html"


class _FileSummary(BaseModel):
    """Typed view over one file's raw coverage counts."""

    covered_lines: int
    num_statements: int
    covered_branches: int
    num_branches: int


class _FileData(BaseModel):
    """Typed view over one file's coverage entry (only the summary is used)."""

    summary: _FileSummary


class _CoverageJson(BaseModel):
    """Typed view over the ``coverage json`` report (only per-file summaries)."""

    files: dict[str, _FileData]


def _gate_env() -> dict[str, str]:
    """Return the subprocess environment with the data file pinned to coverage/."""
    return {**os.environ, "COVERAGE_FILE": str(_DATA_FILE)}


def _clear_stale_outputs() -> None:
    """Remove stale coverage data and reports so the run is always fresh."""
    for path in (_DATA_FILE, _JSON_FILE):
        if path.exists():
            path.unlink()
    if _HTML_DIR.exists():
        shutil.rmtree(_HTML_DIR)


def _run_suite() -> int:
    """Run the full default suite under coverage; return the child exit code.

    The suite runs with its normal default markers/config (no extra pytest
    args); branch coverage and the source list come from the pyproject
    ``[tool.coverage.*]`` config, and the data file is pinned to
    ``coverage/.coverage`` via the environment.
    """
    completed = subprocess.run(
        [sys.executable, "-m", "coverage", "run", "-m", "pytest"],
        env=_gate_env(),
        check=False,
    )
    return completed.returncode


def _generate_reports() -> int:
    """Generate the JSON and HTML reports under coverage/; return 0 on success."""
    json_run = subprocess.run(
        [sys.executable, "-m", "coverage", "json"],
        env=_gate_env(),
        check=False,
    )
    if json_run.returncode != 0:
        rc = json_run.returncode
        msg = f"error: `coverage json` failed with exit {rc}; refusing to pass.\n"
        sys.stderr.write(msg)
        return 1
    html_run = subprocess.run(
        [sys.executable, "-m", "coverage", "html"],
        env=_gate_env(),
        check=False,
    )
    if html_run.returncode != 0:
        rc = html_run.returncode
        msg = f"error: `coverage html` failed with exit {rc}; refusing to pass.\n"
        sys.stderr.write(msg)
        return 1
    return 0


def _combined_percentage() -> tuple[float, list[tuple[str, int, int]]]:
    """Compute the exact combined percentage and per-file (path, covered, total).

    The combined percentage is the exact float fraction of covered units over
    total units, where units are statements plus branches summed across all
    files in the JSON report.
    """
    report = _CoverageJson.model_validate(
        json.loads(_JSON_FILE.read_text(encoding="utf-8")),
    )
    per_file: list[tuple[str, int, int]] = []
    covered_total = 0
    total_total = 0
    for path in sorted(report.files):
        summary = report.files[path].summary
        covered = summary.covered_lines + summary.covered_branches
        total = summary.num_statements + summary.num_branches
        per_file.append((path, covered, total))
        covered_total += covered
        total_total += total
    if total_total == 0:
        return 0.0, per_file
    return covered_total / total_total * 100.0, per_file


def _print_table(per_file: list[tuple[str, int, int]]) -> None:
    """Print the per-file covered/total unit table."""
    for path, covered, total in per_file:
        pct = covered / total * 100.0 if total else 0.0
        sys.stdout.write(f"{path}\t{covered}/{total}\t{pct:.2f}%\n")


def main() -> int:
    """Run the fresh full-suite combined coverage gate; return the exit code."""
    _clear_stale_outputs()

    suite_rc = _run_suite()
    if suite_rc != 0:
        rc = suite_rc
        msg = f"error: the test suite failed with exit {rc}; the gate never passes.\n"
        sys.stderr.write(msg)
        return 1

    if not _DATA_FILE.exists():
        data = _DATA_FILE
        msg = f"error: suite produced no coverage data ({data}); refusing to pass.\n"
        sys.stderr.write(msg)
        return 1

    if _generate_reports() != 0:
        return 1

    combined, per_file = _combined_percentage()
    sys.stdout.write(
        f"combined coverage: {combined!r}% (required >= {REQUIRED_COMBINED!r}%)\n",
    )
    _print_table(per_file)

    if combined < REQUIRED_COMBINED:
        pct = combined
        req = REQUIRED_COMBINED
        msg = f"error: combined coverage {pct!r}% is below the required {req!r}%.\n"
        sys.stderr.write(msg)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
