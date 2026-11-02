"""Sphinx configuration for the oc-selector documentation site."""

from __future__ import annotations

project = "OpenCode Selector"
html_title = "OpenCode Selector"
needs_sphinx = "9.0.0"
extensions = ["autoapi.extension"]
html_theme = "furo"
nitpicky = True
# External (non-oc_selector) types referenced in annotations are not documented
# in this project; ignore them so the strict (-W) build stays warning-free.
nitpick_ignore = [
    ("py:class", "pathlib.Path"),
    ("py:class", "typer.Option"),
    ("py:class", "typer.Argument"),
    ("py:class", "json5.model.JSONText"),
    ("py:class", "json5.model.Node"),
    ("py:class", "pydantic.ConfigDict"),
    ("py:obj", "pydantic.BaseModel"),
    ("py:obj", "enum.StrEnum"),
]
autoapi_dirs = ["../src"]
autoapi_root = "reference/api"
autoapi_keep_files = True
autoapi_add_toctree_entry = False
autoapi_options = [
    "members",
    "undoc-members",
    "show-inheritance",
    "show-module-summary",
]
exclude_patterns = ["_build", "_templates"]
html_static_path = ["_static"]
