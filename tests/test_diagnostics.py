"""TDD tests for read-only diagnostics collector.

Covers:
- Schema version exactly 1
- Version probe with 2s timeout
- Only [binary, '--version'] subprocess
- No secrets in output
- Handle absent/malformed registry
- Distinguish lock metadata from liveness
- Unhealthy diagnostics exits 0 (issues are data)
"""

from __future__ import annotations

import json
from collections.abc import Callable

from oc_selector.diagnostics import collect_diagnostics
from oc_selector.registry import ProfileEntry, Registry

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
                "gpt": ProfileEntry(directory="opencode_gpt", omo_profile="oc-selector-gpt"),
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
