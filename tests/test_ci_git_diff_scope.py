"""TDD tests for the CI change-scope classifier (``tools.ci_git_diff_scope``).

These classify the set of changed paths in a push as ``code``, ``docs`` or
``other`` and decide whether the docs site must re-render. They are written
first (red): the module under test does not exist yet, so import fails until the
implementation todo provides ``Scope``, ``classify``, ``render`` and ``main``.

The classifier is stdlib-only and matches paths with ``pathlib.PurePath.full_match``
(glob semantics, NOT ``fnmatch``) so a zero-component ``**`` matches a top-level
path: ``docs/index.rst`` matches ``docs/**/*.rst``.
"""

from __future__ import annotations

import io
from pathlib import Path, PurePosixPath
from typing import Final

import pytest

from tools.ci_git_diff_scope import Scope, classify, main, render

# Representative oc-selector path sets: (name, changed paths, expected scope,
# expected render). ``render`` mirrors oc-selector's actual Sphinx render inputs
# (``src/**/*.py``, ``docs/**/*.rst``, ``docs/conf.py``, ...); it excludes ``.md``.
_CASES: Final = [
    pytest.param(
        ["docs/how-to/guide.rst", "docs/index.rst"],
        Scope.DOCS,
        True,
        id="top_and_nested_rst",
    ),
    pytest.param(
        ["docs/explanation/foo.rst"],
        Scope.DOCS,
        True,
        id="nested_rst",
    ),
    pytest.param(
        ["docs/conf.py"],
        Scope.CODE,
        True,
        id="conf_py",
    ),
    pytest.param(
        ["src/oc_selector/cli.py"],
        Scope.CODE,
        True,
        id="src_cli",
    ),
    pytest.param(
        ["tests/test_ci_git_diff_scope.py"],
        Scope.CODE,
        False,
        id="test_file",
    ),
    pytest.param(
        ["tests/conftest.py"],
        Scope.CODE,
        False,
        id="conftest",
    ),
    pytest.param(
        ["README.md"],
        Scope.DOCS,
        False,
        id="root_readme",
    ),
    pytest.param(
        ["docs/README.md"],
        Scope.DOCS,
        False,
        id="docs_readme",
    ),
    pytest.param(
        ["LICENSE"],
        Scope.OTHER,
        False,
        id="license",
    ),
    pytest.param(
        [".gitignore"],
        Scope.OTHER,
        False,
        id="gitignore",
    ),
    pytest.param(
        ["notes/scratch.txt"],
        Scope.CODE,
        False,
        id="unknown_defaults_to_code",
    ),
    pytest.param(
        [],
        Scope.OTHER,
        False,
        id="empty_set",
    ),
    pytest.param(
        ["docs/x.rst", "src/y.py"],
        Scope.CODE,
        True,
        id="mixed_code_and_docs",
    ),
    pytest.param(
        ["docs/x.rst", "README.md"],
        Scope.DOCS,
        True,
        id="mixed_docs_and_readme",
    ),
    pytest.param(
        ["docs/_static/custom.css"],
        Scope.CODE,
        True,
        id="docs_static_css_defaults_to_code",
    ),
    pytest.param(
        [".github/workflows/README.md"],
        Scope.DOCS,
        False,
        id="ci_readme_is_docs",
    ),
    pytest.param(
        [".github/workflows/ci.yml"],
        Scope.CODE,
        False,
        id="ci_workflow_is_code",
    ),
    pytest.param(
        ["uv.lock"],
        Scope.CODE,
        True,
        id="uv_lock_code_and_render",
    ),
    pytest.param(
        ["justfile"],
        Scope.CODE,
        False,
        id="justfile_code_no_render",
    ),
    pytest.param(
        ["docs/my guide.rst"],
        Scope.DOCS,
        True,
        id="docs_rst_internal_space",
    ),
    pytest.param(
        ["docs/x.rst", "README.md", "src/y.py"],
        Scope.CODE,
        True,
        id="mixed_three_code",
    ),
]


@pytest.mark.parametrize(
    ("paths", "expected_scope", "expected_render"),
    _CASES,
)
def test_classify_and_render(
    paths: list[str],
    expected_scope: Scope,
    expected_render: bool,  # noqa: FBT001  # parametrized test expectation
) -> None:
    """Classify and render a set of changed paths.

    Given: a set of changed paths from a push. When: they are passed to
    ``classify`` and ``render``. Then: the scope follows the docs -> other ->
    code ordering (an unknown path defaults to ``code``) and ``render`` reflects
    the Sphinx render inputs.
    """
    assert classify(paths) == expected_scope
    assert render(paths) is expected_render


def test_full_match_zero_component_double_star() -> None:
    """Assert ``full_match`` glob semantics, not ``fnmatch``.

    Given: a top-level ``docs/index.rst`` and the glob ``docs/**/*.rst``. When:
    they are matched with ``PurePosixPath.full_match``. Then: it is True — the
    zero-component ``**`` match every tier depends on.
    """
    assert PurePosixPath("docs/index.rst").full_match("docs/**/*.rst") is True


def test_scope_is_str_enum_values() -> None:
    """Assert the ``Scope`` enum carries the exact string values.

    Given: the ``Scope`` enum. When: its members are read. Then: ``code``,
    ``docs`` and ``other`` are their string values.
    """
    assert Scope.CODE.value == "code"
    assert Scope.DOCS.value == "docs"
    assert Scope.OTHER.value == "other"


def test_cli_writes_scope_triple_to_stdout(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The CLI prints the ``scope``/``render``/``full_gate`` triple to stdout.

    Given: no ``$GITHUB_OUTPUT`` (a runner sets it for the whole job, so it is
    removed here to exercise the stdout path). When: ``main`` reads a code path
    from stdin. Then: it returns 0 and prints the triple, with ``full_gate`` true
    because a code push runs every gate.
    """
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO("src/oc_selector/cli.py\n"))
    assert main() == 0
    out = capsys.readouterr().out
    assert "scope=code" in out
    assert "render=true" in out
    assert "full_gate=true" in out


def test_cli_writes_scope_triple_to_github_output_file(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """The CLI writes the triple to ``$GITHUB_OUTPUT`` when it is set.

    Given: ``$GITHUB_OUTPUT`` pointing at a temp file (as on a runner). When:
    ``main`` reads a docs path from stdin. Then: it returns 0, prints nothing to
    stdout, and appends the triple to the file with ``full_gate`` false.
    """
    out_file = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out_file))
    monkeypatch.setattr("sys.stdin", io.StringIO("docs/index.rst\n"))
    assert main() == 0
    assert capsys.readouterr().out == ""
    contents = out_file.read_text()
    assert "scope=docs" in contents
    assert "render=true" in contents
    assert "full_gate=false" in contents


def test_cli_preserves_internal_space_in_path(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The CLI preserves internal spaces in a path.

    Given: a docs path with an internal space on stdin (``docs/my guide.rst``).
    When: ``main`` reads it (it strips only leading/trailing whitespace). Then:
    it returns 0, classifies the path as ``docs`` (proving the space survived the
    round-trip unchanged), and prints the triple to stdout.
    """
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO("docs/my guide.rst\n"))
    assert main() == 0
    out = capsys.readouterr().out
    assert "scope=docs" in out
    assert "render=true" in out
