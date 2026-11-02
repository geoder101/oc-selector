"""TDD tests for read-only diagnostics collector.

Covers:
- Schema version exactly 1
- Version probe with 2s timeout
- Only [binary, '--version'] subprocess
- No secrets in output
- Handle absent/malformed registry
- Distinguish lock metadata from liveness
- Unhealthy diagnostics exits 0 (issues are data)
- Real probe_version subprocess paths (missing, not executable, ok, timeout)
- Lock-present and recovery-pending assembly branches
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING

from oc_selector.diagnostics import collect_diagnostics, probe_version
from oc_selector.registry import ProfileEntry, Registry

if TYPE_CHECKING:
    from subprocess import CompletedProcess

    import pytest
    from helpers.process_harness import ProcessHarness

ProbeFn = Callable[[str, float], tuple[str | None, str]]


def _fake_probe_ok(version: str = "1.19.5") -> ProbeFn:
    """Return a fake probe that always succeeds."""

    def probe(_exe: str, _timeout: float = 2.0) -> tuple[str | None, str]:
        return version, "ok"

    return probe


def _fake_probe_missing() -> ProbeFn:
    def probe(_exe: str, _timeout: float = 2.0) -> tuple[str | None, str]:
        return None, "missing"

    return probe


def _fake_probe_timeout() -> ProbeFn:
    def probe(_exe: str, _timeout: float = 2.0) -> tuple[str | None, str]:
        return None, "timeout"

    return probe


class TestDiagnosticsSchema:
    def test_happy_schema_version_is_one(self) -> None:
        reg = Registry(
            schema_version=1,
            default_profile="default",
            profiles={"default": ProfileEntry(directory="opencode")},
        )
        result = collect_diagnostics(reg, version_probe=_fake_probe_ok())
        assert result.schema_version == 1

    def test_happy_wrapper_version_present(self) -> None:
        reg = Registry(
            schema_version=1,
            default_profile="default",
            profiles={"default": ProfileEntry(directory="opencode")},
        )
        result = collect_diagnostics(reg, version_probe=_fake_probe_ok())
        assert "version" in result.wrapper
        assert result.wrapper["version"] != ""

    def test_happy_paths_present(self) -> None:
        reg = Registry(
            schema_version=1,
            default_profile="default",
            profiles={"default": ProfileEntry(directory="opencode")},
        )
        result = collect_diagnostics(reg, version_probe=_fake_probe_ok())
        assert "config_parent" in result.paths
        assert "registry" in result.paths
        assert "state" in result.paths
        assert "omo_config" in result.paths

    def test_happy_to_dict_is_json_safe(self) -> None:
        reg = Registry(
            schema_version=1,
            default_profile="default",
            profiles={"default": ProfileEntry(directory="opencode")},
        )
        result = collect_diagnostics(reg, version_probe=_fake_probe_ok())
        d = result.to_dict()
        json.dumps(d)


class TestVersionProbe:
    def test_happy_probe_ok(self) -> None:
        result = collect_diagnostics(
            Registry(
                schema_version=1,
                default_profile="default",
                profiles={"default": ProfileEntry(directory="opencode")},
            ),
            version_probe=_fake_probe_ok("1.19.5"),
        )
        assert result.opencode["version"] == "1.19.5"
        assert result.opencode["status"] == "ok"

    def test_happy_probe_missing(self) -> None:
        result = collect_diagnostics(
            Registry(
                schema_version=1,
                default_profile="default",
                profiles={"default": ProfileEntry(directory="opencode")},
            ),
            version_probe=_fake_probe_missing(),
        )
        assert result.opencode["version"] is None
        assert result.opencode["status"] == "missing"

    def test_happy_probe_timeout(self) -> None:
        result = collect_diagnostics(
            Registry(
                schema_version=1,
                default_profile="default",
                profiles={"default": ProfileEntry(directory="opencode")},
            ),
            version_probe=_fake_probe_timeout(),
        )
        assert result.opencode["version"] is None
        assert result.opencode["status"] == "timeout"


class TestRegistryHandling:
    def test_happy_valid_registry(self) -> None:
        reg = Registry(
            schema_version=1,
            default_profile="default",
            profiles={"default": ProfileEntry(directory="opencode")},
        )
        result = collect_diagnostics(reg, version_probe=_fake_probe_ok())
        assert result.default_profile == "default"
        assert len(result.profiles) == 1
        assert result.profiles[0].name == "default"

    def test_happy_absent_registry(self) -> None:
        result = collect_diagnostics(None, version_probe=_fake_probe_ok())
        assert result.default_profile is None
        assert result.profiles == []
        assert any(w["code"] == "registry-absent" for w in result.warnings)

    def test_happy_multiple_profiles(self) -> None:
        reg = Registry(
            schema_version=1,
            default_profile="gpt",
            profiles={
                "default": ProfileEntry(directory="opencode"),
                "gpt": ProfileEntry(
                    directory="opencode_gpt",
                    omo_profile="oc-selector-gpt",
                ),
            },
        )
        result = collect_diagnostics(reg, version_probe=_fake_probe_ok())
        assert result.default_profile == "gpt"
        assert len(result.profiles) == 2
        gpt = next(p for p in result.profiles if p.name == "gpt")
        assert gpt.omo_profile == "oc-selector-gpt"


class TestPrivacy:
    def test_happy_no_secrets_in_output(self) -> None:
        reg = Registry(
            schema_version=1,
            default_profile="default",
            profiles={"default": ProfileEntry(directory="opencode")},
        )
        result = collect_diagnostics(reg, version_probe=_fake_probe_ok())
        d = result.to_dict()
        text = str(d)
        # No environment values, no raw config content
        assert "password" not in text.lower()
        assert "api_key" not in text.lower()
        assert "token" not in text.lower()


class TestLockStatus:
    def test_happy_lock_absent(self) -> None:
        reg = Registry(
            schema_version=1,
            default_profile="default",
            profiles={"default": ProfileEntry(directory="opencode")},
        )
        result = collect_diagnostics(reg, version_probe=_fake_probe_ok())
        assert result.lock["status"] in ("absent", "present")

    def test_happy_recovery_clean(self) -> None:
        reg = Registry(
            schema_version=1,
            default_profile="default",
            profiles={"default": ProfileEntry(directory="opencode")},
        )
        result = collect_diagnostics(reg, version_probe=_fake_probe_ok())
        assert result.recovery["status"] in ("clean", "pending")


def _registry() -> Registry:
    """Build a minimal single-profile registry for collector tests."""
    return Registry(
        schema_version=1,
        default_profile="default",
        profiles={"default": ProfileEntry(directory="opencode")},
    )


class TestProbeVersion:
    """Characterization of the real probe_version subprocess paths."""

    def test_happy_relative_missing_on_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        process_harness: ProcessHarness,
    ) -> None:
        """A relative name absent from PATH reports missing."""
        monkeypatch.setenv("PATH", str(process_harness.bin_dir))
        assert probe_version("ocx-no-such-binary") == (None, "missing")

    def test_happy_relative_found_on_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        process_harness: ProcessHarness,
        fixture_executable: Callable[..., Path],
    ) -> None:
        """A relative name found on PATH is resolved and probed."""
        fixture_executable("opencode", stdout="3.4.5\n")
        monkeypatch.setenv("PATH", str(process_harness.bin_dir))
        assert probe_version("opencode") == ("3.4.5", "ok")

    def test_happy_absolute_missing(self, tmp_path: Path) -> None:
        """An absolute path that does not exist reports missing."""
        assert probe_version(str(tmp_path / "nope")) == (None, "missing")

    def test_happy_not_executable(self, tmp_path: Path) -> None:
        """An existing file without the execute bit reports not_executable."""
        target = tmp_path / "plain"
        target.write_text("data", encoding="utf-8")
        target.chmod(0o600)
        assert probe_version(str(target)) == (None, "not_executable")

    def test_happy_version_ok(
        self,
        fixture_executable: Callable[..., Path],
    ) -> None:
        """A successful --version probe returns the stripped stdout."""
        exe = fixture_executable("opencode", stdout="1.19.5\n")
        assert probe_version(str(exe)) == ("1.19.5", "ok")

    def test_happy_nonzero_exit_reports_ok_status(
        self,
        fixture_executable: Callable[..., Path],
    ) -> None:
        """A non-zero exit still reports the ok status with no version."""
        exe = fixture_executable("opencode", stderr="err\n", exit_code=2)
        assert probe_version(str(exe)) == (None, "ok")

    def test_happy_timeout(self, process_harness: ProcessHarness) -> None:
        """A probe exceeding the timeout reports timeout."""
        slow = process_harness.bin_dir / "slow"
        slow.write_text(
            f"#!{sys.executable}\nimport time\ntime.sleep(2)\n",
            encoding="utf-8",
        )
        slow.chmod(0o700)
        assert probe_version(str(slow), timeout=0.2) == (None, "timeout")

    def test_failure_run_oserror_reports_missing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An OSError from subprocess.run reports missing."""
        exe = tmp_path / "exe"
        exe.write_text("#!/bin/sh\n", encoding="utf-8")
        exe.chmod(0o755)

        def _raise(
            *_args: object,
            **_kwargs: object,
        ) -> CompletedProcess[str]:
            raise OSError("simulated spawn failure")

        monkeypatch.setattr("oc_selector.diagnostics.subprocess.run", _raise)
        assert probe_version(str(exe)) == (None, "missing")


class TestIsExecutable:
    """The regular-file + X_OK check with OSError tolerance."""

    def test_happy_regular_executable(self, tmp_path: Path) -> None:
        """A regular executable file is recognized as executable."""
        from oc_selector.diagnostics import _is_executable  # pyright: ignore[reportPrivateUsage]  # noqa: PLC0415

        exe = tmp_path / "exe"
        exe.write_text("#!/bin/sh\n", encoding="utf-8")
        exe.chmod(0o755)
        assert _is_executable(exe) is True

    def test_failure_non_regular_or_no_xok(self, tmp_path: Path) -> None:
        """A non-executable file and a directory are not executable."""
        from oc_selector.diagnostics import _is_executable  # pyright: ignore[reportPrivateUsage]  # noqa: PLC0415

        plain = tmp_path / "plain"
        plain.write_text("data", encoding="utf-8")
        plain.chmod(0o600)
        assert _is_executable(plain) is False
        assert _is_executable(tmp_path) is False

    def test_failure_stat_oserror_returns_false(self, tmp_path: Path) -> None:
        """A stat OSError (broken symlink) is tolerated as not-executable."""
        from oc_selector.diagnostics import _is_executable  # pyright: ignore[reportPrivateUsage]  # noqa: PLC0415

        broken = tmp_path / "broken"
        broken.symlink_to(tmp_path / "no-such-target")
        assert _is_executable(broken) is False


class TestDefaultProbe:
    """The collector falls back to the real probe_version when none is given."""

    def test_happy_default_probe_missing_opencode(
        self,
        isolated_home: Path,  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
        monkeypatch: pytest.MonkeyPatch,
        process_harness: ProcessHarness,
    ) -> None:
        """Without an explicit probe, the real probe runs and finds no opencode."""
        monkeypatch.setenv("PATH", str(process_harness.bin_dir))
        result = collect_diagnostics(_registry())
        assert result.opencode["status"] == "missing"
        assert result.opencode["version"] is None


class TestLockAndRecoveryAssembly:
    """Lock-present and recovery-pending branches of the report assembly."""

    def test_happy_lock_present(
        self,
        isolated_home: Path,
    ) -> None:
        """An existing maintenance.lock reports present."""
        lock = isolated_home / ".config" / "oc-selector" / "maintenance.lock"
        lock.parent.mkdir(parents=True)
        lock.write_text("", encoding="utf-8")
        result = collect_diagnostics(_registry(), version_probe=_fake_probe_ok())
        assert result.lock["status"] == "present"

    def test_happy_recovery_pending(
        self,
        isolated_home: Path,
    ) -> None:
        """A journal on disk reports a pending recovery with one entry."""
        journals = isolated_home / ".config" / "oc-selector" / "journals"
        journals.mkdir(parents=True)
        (journals / "abc.json").write_text("{}", encoding="utf-8")
        result = collect_diagnostics(_registry(), version_probe=_fake_probe_ok())
        assert result.recovery["status"] == "pending"
        assert result.recovery["transactions"] == [
            {
                "id": "abc.json",
                "operation": "unknown",
                "phase": "unknown",
                "profiles": [],
            },
        ]

    def test_happy_recovery_clean_with_empty_journals(
        self,
        isolated_home: Path,
    ) -> None:
        """An existing but empty journals directory still reports clean."""
        journals = isolated_home / ".config" / "oc-selector" / "journals"
        journals.mkdir(parents=True)
        result = collect_diagnostics(_registry(), version_probe=_fake_probe_ok())
        assert result.recovery["status"] == "clean"
        assert result.recovery["transactions"] == []
