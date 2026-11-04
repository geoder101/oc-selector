"""Foundational TDD tests for packaging, isolation, typed errors and subprocess.

These tests establish the foundational contract: a clean package with a single console
script, no import side effects, a typed error hierarchy with public exit codes,
strict isolation from the real home, and a working isolated subprocess harness.
"""

from __future__ import annotations

import json
import os
import sys
import tomllib
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from helpers.process_harness import ProcessHarness
from pydantic import TypeAdapter

import oc_selector
from oc_selector import cli, errors
from oc_selector.paths import default_profile_directory, profile_directory

if TYPE_CHECKING:
    from collections.abc import Callable

    from conftest import HomeGuard

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

_SCRIPTS_ADAPTER: TypeAdapter[dict[str, str]] = TypeAdapter(dict[str, str])
_VERSION_ADAPTER: TypeAdapter[str] = TypeAdapter(str)
_ERROR_CLASS_ADAPTER: TypeAdapter[type[errors.OcSelectorError]] = TypeAdapter(
    type[errors.OcSelectorError],
)
_RECORD_ADAPTER: TypeAdapter[dict[str, object]] = TypeAdapter(dict[str, object])


class TestPackaging:
    """Packaging metadata, import safety and versioning."""

    def test_happy_metadata_has_single_console_script(self) -> None:
        """pyproject.toml declares exactly one console script: oc-selector."""
        pyproject = _PROJECT_ROOT / "pyproject.toml"
        with pyproject.open("rb") as handle:
            data = tomllib.load(handle)
        scripts = _SCRIPTS_ADAPTER.validate_python(data["project"]["scripts"])
        assert list(scripts) == ["oc-selector"]
        assert scripts["oc-selector"] == "oc_selector.cli:main"

    def test_happy_import_has_no_side_effects(
        self,
        process_harness: ProcessHarness,
    ) -> None:
        """Importing oc_selector in a fresh process creates no files or dirs."""
        script = (
            "import os\n"
            "before = sorted(os.listdir('.'))\n"
            "import oc_selector\n"
            "after = sorted(os.listdir('.'))\n"
            "assert before == after, (before, after)\n"
            "print('clean')\n"
        )
        result = process_harness.run([sys.executable, "-c", script])
        assert result.exit_code == 0, result.stderr
        assert "clean" in result.stdout

    def test_happy_version_is_defined(self) -> None:
        """oc_selector.__version__ is a valid semver string."""
        version = oc_selector.__version__
        assert isinstance(version, str)
        parts = version.split(".")
        assert len(parts) == 3
        for part in parts:
            assert part.isdigit()

    def test_happy_version_matches_declared_version(self) -> None:
        """__version__ and the installed metadata match pyproject.toml."""
        pyproject = _PROJECT_ROOT / "pyproject.toml"
        with pyproject.open("rb") as handle:
            data = tomllib.load(handle)
        declared = _VERSION_ADAPTER.validate_python(data["project"]["version"])
        assert version("oc-selector") == declared
        assert oc_selector.__version__ == declared

    def test_happy_entrypoint_is_callable(self) -> None:
        """The console-script target oc_selector.cli.main exists and is callable."""
        assert callable(cli.main)


class TestTypedErrors:
    """Typed error hierarchy with public exit classifications."""

    def test_happy_error_hierarchy(self) -> None:
        """All public errors inherit from OcSelectorError with code and message."""
        names = (
            "ArgumentError",
            "RegistryError",
            "MaintenanceError",
            "ExecutableMissingError",
            "ExecutableNotExecutableError",
            "RecoveryError",
            "TransactionError",
            "SchemaValidationError",
            "NetworkError",
            "InterruptionError",
        )
        for name in names:
            cls = _ERROR_CLASS_ADAPTER.validate_python(getattr(errors, name))
            assert issubclass(cls, errors.OcSelectorError)
            err = cls("msg")
            assert err.message == "msg"
            assert isinstance(err.code, str)
            assert err.code
            assert str(err) == "msg"

    def test_failure_wrong_exit_code_detected(self) -> None:
        """Any deviation from the exit-code contract is detected."""
        contract = {
            errors.ArgumentError: 2,
            errors.RegistryError: 2,
            errors.MaintenanceError: 1,
            errors.ExecutableMissingError: 127,
            errors.ExecutableNotExecutableError: 126,
            errors.RecoveryError: 1,
            errors.TransactionError: 1,
            errors.SchemaValidationError: 1,
            errors.NetworkError: 1,
            errors.InterruptionError: 130,
        }
        for cls, expected in contract.items():
            assert cls.exit_code == expected, f"{cls.__name__} exit_code != {expected}"


class TestIsolation:
    """Isolation of the test environment from the real home."""

    def test_happy_isolated_home(self, isolated_home: Path) -> None:
        """Fixtures create isolated HOME/XDG_CONFIG_HOME/cwd."""
        assert os.environ["HOME"] == str(isolated_home)
        assert os.environ["USERPROFILE"] == str(isolated_home)
        assert os.environ["XDG_CONFIG_HOME"] == str(isolated_home / ".config")
        assert Path.cwd() == isolated_home
        assert (isolated_home / ".config").is_dir()

    def test_failure_personal_home_access_denied(
        self,
        deny_personal_home: HomeGuard,
    ) -> None:
        """Attempting to access the real home directory is detected."""
        # A path inside the isolated home is allowed.
        deny_personal_home.assert_isolated(deny_personal_home.isolated_home / "ok.txt")
        # A path in the real home is detected as a violation.
        with pytest.raises(AssertionError):
            deny_personal_home.assert_isolated(
                deny_personal_home.real_home / "evil.txt",
            )


class TestPaths:
    """Portable path resolution helpers."""

    def test_happy_profile_directory(self) -> None:
        """profile_directory joins the profile name under the config parent."""
        assert profile_directory(Path("/cfg"), "work") == Path("/cfg/work")

    def test_happy_default_profile_directory(self) -> None:
        """default_profile_directory namespaces new profiles under the state root."""
        assert default_profile_directory("work") == "oc-selector/profiles/work"


class TestSubprocessHarness:
    """Isolated subprocess harness roundtrip, spaces, failure and verify.py."""

    def test_happy_process_roundtrip(
        self,
        process_harness: ProcessHarness,
        fixture_executable: Callable[..., Path],
    ) -> None:
        """Fixture executable records argv, stdout, stderr and exit code."""
        exe = fixture_executable(
            "opencode",
            stdout="hello\n",
            stderr="warn\n",
            exit_code=0,
        )
        result = process_harness.run([exe, "run", "--flag"], stdin="stdin-data")
        assert result.exit_code == 0
        assert result.stdout == "hello\n"
        assert result.stderr == "warn\n"
        record = process_harness.record_for("opencode")
        argv = record["argv"]
        assert isinstance(argv, list)
        assert argv[1:] == ["run", "--flag"]
        assert record["stdin"] == "stdin-data"
        assert result.record_path.is_file()

    def test_happy_paths_with_spaces(self) -> None:
        """Fixture executables work when the sandbox path contains spaces."""
        with ProcessHarness(prefix="ocx harness with spaces-") as harness:
            assert " " in str(harness.root)
            exe = harness.create_executable("opencode", stdout="ok\n")
            result = harness.run([exe, "arg with spaces"])
            assert result.exit_code == 0
            assert result.stdout == "ok\n"
            record = harness.record_for("opencode")
            argv = record["argv"]
            assert isinstance(argv, list)
            assert argv[1:] == ["arg with spaces"]

    def test_failure_child_failure_captured(
        self,
        process_harness: ProcessHarness,
        fixture_executable: Callable[..., Path],
    ) -> None:
        """Non-zero exit codes and stderr are captured correctly."""
        exe = fixture_executable(
            "opencode",
            stdout="partial\n",
            stderr="boom\n",
            exit_code=3,
        )
        result = process_harness.run([exe, "run"])
        assert result.exit_code == 3
        assert result.stderr == "boom\n"
        assert result.stdout == "partial\n"
        recorded = _RECORD_ADAPTER.validate_python(
            json.loads(result.record_path.read_text(encoding="utf-8")),
        )
        assert recorded["exit_code"] == 3
        assert recorded["stderr"] == "boom\n"

    def test_happy_expect_exit_metadata(self, tmp_path: Path) -> None:
        """verify.py --expect-exit records an expected failure as metadata."""
        verify = _PROJECT_ROOT / "tools" / "verify.py"
        evidence = tmp_path / "evidence.txt"
        child = tmp_path / "child.py"
        child.write_text(
            "import sys\nsys.stderr.write('expected-fail\\n')\nsys.exit(4)\n",
            encoding="utf-8",
        )
        argv = [
            sys.executable,
            str(verify),
            "--evidence",
            str(evidence),
            "--expect-exit",
            "4",
            "--",
            sys.executable,
            str(child),
        ]
        with ProcessHarness() as harness:
            result = harness.run(argv)
        assert result.exit_code == 0
        text = evidence.read_text(encoding="utf-8")
        assert "expectation: matched" in text
        assert "return_code: 4" in text
        assert "expected-fail" in text
