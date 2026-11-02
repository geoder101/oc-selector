# oc-selector documentation

This directory is the source for the hosted documentation site. It is built with
[Sphinx](https://www.sphinx-doc.org/) and the [Furo](https://furo.readthedocs.io/)
theme, and the API reference is generated from the source by
[sphinx-autoapi](https://www.sphinx-autoapi.com/).

## What is authored vs. generated

- **Authored** (checked into git): `conf.py`, `index.rst`, the four Diataxis
  section directories (`tutorials/`, `how-to/`, `reference/`, `explanation/`),
  `_static/`, and this file.
- **Generated** (git-ignored, rebuilt on every build): `docs/_build/` (the build
  output) and `docs/reference/api/` (the AutoAPI-generated API reference, written
  next to the source because `autoapi_keep_files` is on).

You never edit the generated files by hand; they are produced from `src/oc_selector`
each time the site is built.

## Building and previewing

The build is strict: it runs with `-W`, so **any** Sphinx warning is a failure.
A green build is the acceptance gate.

```bash
just docs-check          # clean build, then a strict (-W) HTML build
just docs-preview 8000   # build, then serve docs/_build/html on port 8000
```

`just docs-preview` depends on `just docs-check`, so it always serves a
warning-free build. Open `http://localhost:8000` in a browser.

### Without Just

On a machine without [just](https://github.com/casey/just), the raw equivalents are:

```bash
uv run --group docs sphinx-build -M clean docs docs/_build
uv run --group docs sphinx-build -M html docs docs/_build -E -a -W
```

## Public surface

The generated API reference under `reference/api/` documents the **internal**
Python modules of the `oc_selector` package. It is **not** a supported public API:
the only supported interface is the `oc-selector` command-line tool. Internal
modules are implementation details and may change without notice.
