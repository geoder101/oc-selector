"""oc-selector: Cross-platform OpenCode settings selector."""

from __future__ import annotations

from importlib.metadata import version

# The version's single source of truth is pyproject.toml; this reads it from
# the installed distribution so the two can never drift.
__version__ = version("oc-selector")
