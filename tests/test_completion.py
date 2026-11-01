"""TDD tests for non-mutating four-shell completion.

Covers:
- All four shells generate valid scripts
- Profile names included in completion
- Management commands included
- Alias validation
- Unsupported shell rejected
- No file I/O, no subprocess
- Absent registry yields management-only completion
"""

from __future__ import annotations

from pathlib import Path  # noqa: TC003

import pytest

from oc_selector.completion import generate_completion, validate_alias, validate_shell
from oc_selector.errors import ArgumentError
from oc_selector.registry import ProfileEntry, Registry


def _make_registry() -> Registry:
    return Registry(
        schema_version=1,
        default_profile="default",
        profiles={
            "default": ProfileEntry(directory="opencode"),
            "gpt": ProfileEntry(directory="opencode_gpt"),
            "local": ProfileEntry(directory="opencode_local"),
        },
    )


class TestValidateShell:
    def test_happy_all_shells(self) -> None:
        for shell in ("bash", "zsh", "fish", "powershell"):
            assert validate_shell(shell) == shell

    def test_failure_unsupported(self) -> None:
        with pytest.raises(ArgumentError, match="Unsupported"):
            validate_shell("tcsh")


class TestValidateAlias:
    def test_happy_valid_alias(self) -> None:
        assert validate_alias("oc") == "oc"

    def test_failure_invalid_alias(self) -> None:
        with pytest.raises(ArgumentError):
            validate_alias("Invalid-Name")


class TestGenerateCompletion:
    def test_happy_bash(self) -> None:
        reg = _make_registry()
        script = generate_completion("bash", reg)
        assert "oc-selector" in script
        assert "default" in script
        assert "gpt" in script
        assert "local" in script
        assert "complete" in script

    def test_happy_zsh(self) -> None:
        reg = _make_registry()
        script = generate_completion("zsh", reg)
        assert "oc-selector" in script
        assert "compadd" in script
        assert "default" in script

    def test_happy_fish(self) -> None:
        reg = _make_registry()
        script = generate_completion("fish", reg)
        assert "oc-selector" in script
        assert "complete" in script
        assert "default" in script

    def test_happy_powershell(self) -> None:
        reg = _make_registry()
        script = generate_completion("powershell", reg)
        assert "oc-selector" in script
        assert "Register-ArgumentCompleter" in script
        assert "default" in script

    def test_happy_with_alias(self) -> None:
        reg = _make_registry()
        script = generate_completion("bash", reg, alias="oc")
        assert "oc" in script
        assert "complete" in script

    def test_happy_no_registry(self) -> None:
        script = generate_completion("bash", None)
        assert "oc-selector" in script
        # Management commands still present
        assert "list" in script
        assert "init" in script

    def test_happy_management_commands_present(self) -> None:
        reg = _make_registry()
        for shell in ("bash", "zsh", "fish", "powershell"):
            script = generate_completion(shell, reg)
            assert "list" in script
            assert "init" in script
            assert "create" in script
            assert "set-default" in script

    def test_failure_unsupported_shell(self) -> None:
        reg = _make_registry()
        with pytest.raises(ArgumentError):
            generate_completion("tcsh", reg)

    def test_happy_no_file_writes(self, tmp_path: Path) -> None:
        """Completion generation must not write any files."""
        reg = _make_registry()
        before = set(tmp_path.iterdir())
        generate_completion("bash", reg)
        after = set(tmp_path.iterdir())
        assert before == after
