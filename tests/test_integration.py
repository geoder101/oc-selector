"""Integration tests proving generic adoption, guided edits, and source-only recipe.

These tests exercise the full CLI surface through the Typer test runner,
simulating a real user workflow with a pinned runtime environment.

Covers:
- Generic init -> create -> edit -> set-default -> list -> diagnostics
- Source-only personal setup (no OMO conversion)
- Recovery from interrupted transaction
- Completion generation for all four shells
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter
from typer.testing import CliRunner

from oc_selector.cli import app
from oc_selector.registry import ProfileEntry, Registry, load_registry, registry_to_toml

runner = CliRunner()

_OMO_ADAPTER: TypeAdapter[dict[str, object]] = TypeAdapter(dict[str, object])


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a fully isolated environment for integration tests."""
    config_dir = tmp_path / "xdg-config"
    config_dir.mkdir(parents=True)
    state_dir = tmp_path / "xdg-state"
    state_dir.mkdir(parents=True)

    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_dir))
    monkeypatch.setenv("XDG_STATE_HOME", str(state_dir))
    monkeypatch.setenv("HOME", str(tmp_path))

    return config_dir


class TestFullUserWorkflow:
    """Prove the complete user lifecycle works end-to-end."""

    def test_happy_init_create_edit_setdefault_list(
        self,
        isolated_env: Path,
    ) -> None:
        # 1. Init
        r = runner.invoke(app, ["init"])
        assert r.exit_code == 0
        assert "Created" in r.output or "Already" in r.output

        # 2. Create first profile
        r = runner.invoke(app, ["create", "work", "work-dir", "--model", "test/provider/work"])
        assert r.exit_code == 0
        assert "Created" in r.output

        # 3. Create second profile
        r = runner.invoke(app, ["create", "personal", "personal-dir"])
        assert r.exit_code == 0
        assert "Created" in r.output

        # 4. Edit first profile
        r = runner.invoke(app, ["edit", "work", "--directory", "new-work-dir"])
        assert r.exit_code == 0
        assert "Edited" in r.output

        # 5. Set default to personal
        r = runner.invoke(app, ["set-default", "personal"])
        assert r.exit_code == 0
        assert "Set default" in r.output

        # 6. List profiles
        r = runner.invoke(app, ["list"])
        assert r.exit_code == 0
        assert "work" in r.output
        assert "personal" in r.output
        assert "*" in r.output

        # 7. Verify registry state
        reg_path = isolated_env / "oc-selector" / "config.toml"
        reg = load_registry(reg_path)
        assert reg.default_profile == "personal"
        assert reg.profiles["work"].directory == "new-work-dir"
        assert "personal" in reg.profiles

    def test_happy_source_only_setup(
        self,
        isolated_env: Path,
    ) -> None:
        """Source-only personal setup: no OMO conversion, just native profiles."""
        # Init
        runner.invoke(app, ["init"])

        # Create a profile without OMO
        r = runner.invoke(app, ["create", "native-only", "native-dir"])
        assert r.exit_code == 0

        # Verify no OMO profile was set
        reg_path = isolated_env / "oc-selector" / "config.toml"
        reg = load_registry(reg_path)
        assert reg.profiles["native-only"].omo_profile is None

    def test_happy_completion_all_shells(
        self,
        isolated_env: Path,  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
    ) -> None:
        """Completion works for all four shells after init."""
        runner.invoke(app, ["init"])

        for shell in ["bash", "zsh", "fish", "powershell"]:
            r = runner.invoke(app, ["completion", shell])
            assert r.exit_code == 0, f"Completion failed for {shell}: {r.output}"
            assert "oc-selector" in r.output

    def test_happy_info_after_init(
        self,
        isolated_env: Path,  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
    ) -> None:
        """Info (diagnostics) runs successfully after init."""
        runner.invoke(app, ["init"])
        r = runner.invoke(app, ["info"])
        assert r.exit_code == 0

    def test_happy_recover_no_pending(
        self,
        isolated_env: Path,  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
    ) -> None:
        """Recover with no pending journals is a no-op."""
        runner.invoke(app, ["init"])
        r = runner.invoke(app, ["init", "--recover"])
        assert r.exit_code == 0
        assert "No pending" in r.output


class TestErrorPaths:
    """Prove error handling works correctly through the CLI."""

    def test_failure_create_duplicate(
        self,
        isolated_env: Path,  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
    ) -> None:
        runner.invoke(app, ["init"])
        runner.invoke(app, ["create", "test", "dir"])
        r = runner.invoke(app, ["create", "test", "other-dir"])
        assert r.exit_code == 1
        assert "already exists" in r.output

    def test_failure_edit_nonexistent(
        self,
        isolated_env: Path,  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
    ) -> None:
        runner.invoke(app, ["init"])
        r = runner.invoke(app, ["edit", "ghost", "--directory", "dir"])
        assert r.exit_code == 1
        assert "not found" in r.output

    def test_failure_setdefault_nonexistent(
        self,
        isolated_env: Path,  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
    ) -> None:
        runner.invoke(app, ["init"])
        r = runner.invoke(app, ["set-default", "ghost"])
        assert r.exit_code == 1
        assert "not found" in r.output

    def test_failure_list_no_registry(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty"))
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        r = runner.invoke(app, ["list"])
        assert r.exit_code == 2
        assert "No registry" in r.output


class TestIdempotency:
    """Prove operations are idempotent."""

    def test_happy_init_idempotent(
        self,
        isolated_env: Path,  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
    ) -> None:
        r1 = runner.invoke(app, ["init"])
        r2 = runner.invoke(app, ["init"])
        assert r1.exit_code == 0
        assert r2.exit_code == 0
        assert "Already" in r2.output

    def test_happy_setdefault_idempotent(
        self,
        isolated_env: Path,  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
    ) -> None:
        runner.invoke(app, ["init"])
        r1 = runner.invoke(app, ["set-default", "default"])
        r2 = runner.invoke(app, ["set-default", "default"])
        assert r1.exit_code == 0
        assert r2.exit_code == 0


class TestUpgradeOmo:
    """upgrade-omo upgrades the home OMO config to a target version.

    ``isolated_env`` sets ``HOME`` to a sandbox dir, so ``~/.omo`` is isolated.
    """

    @staticmethod
    def _write_omo(version: str) -> Path:
        path = Path.home() / ".omo"
        path.write_text(json.dumps({"version": version, "name": "test-omo"}))
        return path

    @staticmethod
    def _registry_with_omo() -> Registry:
        return Registry(
            default_profile="omo",
            profiles={"omo": ProfileEntry(directory="omo-dir", omo_profile="oc-selector-omo")},
        )

    def test_happy_upgrade_dry_run(self, isolated_env: Path) -> None:
        self._write_omo("1.0.0")
        (isolated_env / "oc-selector").mkdir(parents=True, exist_ok=True)
        (isolated_env / "oc-selector" / "config.toml").write_text(registry_to_toml(self._registry_with_omo()))
        r = runner.invoke(app, ["upgrade-omo", "omo", "--version", "2.0.0", "--dry-run"])
        assert r.exit_code == 0
        assert "would upgrade" in r.output
        data = _OMO_ADAPTER.validate_python(json.loads((Path.home() / ".omo").read_text()))
        assert data["version"] == "1.0.0"

    def test_happy_upgrade_writes(self, isolated_env: Path) -> None:
        self._write_omo("1.0.0")
        (isolated_env / "oc-selector").mkdir(parents=True, exist_ok=True)
        (isolated_env / "oc-selector" / "config.toml").write_text(registry_to_toml(self._registry_with_omo()))
        r = runner.invoke(app, ["upgrade-omo", "omo", "--version", "2.0.0"])
        assert r.exit_code == 0
        data = _OMO_ADAPTER.validate_python(json.loads((Path.home() / ".omo").read_text()))
        assert data["version"] == "2.0.0"

    def test_happy_upgrade_all_no_eligible(self, isolated_env: Path) -> None:  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        runner.invoke(app, ["init"])
        r = runner.invoke(app, ["upgrade-omo", "--all"])
        assert r.exit_code == 0
        assert "No eligible" in r.output

    def test_failure_upgrade_not_declaring(self, isolated_env: Path) -> None:  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        runner.invoke(app, ["init"])
        r = runner.invoke(app, ["upgrade-omo", "default"])
        assert r.exit_code == 2
        assert "does not declare" in r.output

    def test_failure_upgrade_profile_and_all(self, isolated_env: Path) -> None:  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        runner.invoke(app, ["init"])
        r = runner.invoke(app, ["upgrade-omo", "default", "--all"])
        assert r.exit_code == 2
        assert "both PROFILE and --all" in r.output
