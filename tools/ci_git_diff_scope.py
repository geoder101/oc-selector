"""CI change-scope classifier for oc-selector.

Given the set of paths changed in a push, decide whether the change is
``code``, ``docs`` or ``other`` and whether the docs site must re-render.

The classifier is stdlib-only and matches paths with
``pathlib.PurePosixPath.full_match`` (glob semantics, NOT ``fnmatch``) so a
zero-component ``**`` matches a top-level path: ``docs/index.rst`` matches
``docs/**/*.rst``.

The CLI reads newline-separated changed paths from stdin and emits a
``scope``/``render``/``full_gate`` triple either to ``$GITHUB_OUTPUT`` (when set,
as on a GitHub Actions runner) or to stdout.
"""

from __future__ import annotations

import enum
import os
import sys
from pathlib import Path, PurePosixPath
from typing import Final

__all__ = ["Scope", "classify", "main", "render"]


class Scope(enum.StrEnum):
    """Change category: ``code``, ``docs`` or ``other``."""

    CODE = "code"
    DOCS = "docs"
    OTHER = "other"


# Paths that are docs-only: every changed path must match one of these for the
# push to be classified as ``docs``. ``**/*.md`` intentionally catches CI prose
# (e.g. ``.github/workflows/README.md``) as docs — docs still runs its gate, so
# this is safe.
DOCS_ONLY_PATHS: Final = ("docs/**/*.rst", "docs/**/*.md", "**/*.md")

# Paths that carry no code/docs meaning.
OTHER_PATHS: Final = ("LICENSE", ".gitignore")

# Paths that count as code. Anything not a docs-only or other path falls back to
# code, preserving gate safety for any real code change.
CODE_GLOBS: Final = (
    "src/**/*.py",
    "tests/**/*.py",
    "tests/conftest.py",
    "tools/**/*.py",
    "docs/conf.py",
    "pyproject.toml",
    "uv.lock",
    "justfile",
    ".github/**",
    "tests/e2e/**",
)

# Sphinx render inputs. Excludes ``.md`` (Markdown prose does not trigger a
# docs re-render).
RENDER_GLOBS: Final = (
    "src/**/*.py",
    "docs/**/*.rst",
    "docs/conf.py",
    "docs/_static/**",
    "pyproject.toml",
    "uv.lock",
)


def _matches(path: str, globs: tuple[str, ...]) -> bool:
    """Return True iff ``path`` matches any glob in ``globs`` (glob semantics)."""
    return any(PurePosixPath(path).full_match(glob) for glob in globs)


def classify(changed: list[str]) -> Scope:
    """Classify a set of changed paths as ``code``, ``docs`` or ``other``.

    Given: a list of changed paths from a push. When: they are passed to
    ``classify``. Then: an empty set is ``other``; a set that is a subset of
    ``DOCS_ONLY_PATHS`` is ``docs``; a subset of ``OTHER_PATHS`` is ``other``;
    otherwise (any path that defaults to code) it is ``code``.
    """
    if not changed:
        return Scope.OTHER
    if all(_matches(p, DOCS_ONLY_PATHS) for p in changed):
        return Scope.DOCS
    if all(_matches(p, OTHER_PATHS) for p in changed):
        return Scope.OTHER
    return Scope.CODE


def render(changed: list[str]) -> bool:
    """Return True iff any changed path is a Sphinx render input.

    Given: a list of changed paths. When: they are checked against
    ``RENDER_GLOBS``. Then: True iff at least one path matches (``.md`` never
    triggers a re-render).
    """
    return any(_matches(p, RENDER_GLOBS) for p in changed)


def main() -> int:
    """CLI entry point: classify stdin paths and emit the scope triple.

    Given: newline-separated changed paths on stdin (blank lines stripped,
    internal spaces preserved). When: ``main`` runs. Then: it returns 0 and
    writes the ``scope``/``render``/``full_gate`` triple to ``$GITHUB_OUTPUT``
    (append mode) when that env var is set, otherwise to stdout.
    """
    changed = [line.strip() for line in sys.stdin if line.strip()]
    scope = classify(changed)
    do_render = render(changed)
    full_gate = scope is Scope.CODE
    triple = (
        f"scope={scope.value}\n"
        f"render={'true' if do_render else 'false'}\n"
        f"full_gate={'true' if full_gate else 'false'}"
    )

    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as handle:
            handle.write(triple + "\n")
    else:
        print(triple)  # noqa: T201 - CLI emits its result to stdout

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
