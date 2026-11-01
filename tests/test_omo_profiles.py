"""TDD tests for parameterized legacy conversion and modern native projections.

Covers C3/C10 contracts:
- Legacy field conversion (agents, categories, disabled_hooks, permission)
- Harness key is literal "[opencode]"
- Model chain normalization into singleton arrays
- Unknown fields fail before commit
- Modern native entry building
- Model ID validation
- No author constants in src
"""

from __future__ import annotations

import pytest

from oc_selector.errors import SchemaValidationError
from oc_selector.omo_profiles import (
    HARNESS_KEY,
    ConversionInput,
    build_native_entry,
    convert_legacy_to_native,
    validate_model_id,
)


class TestLegacyConversion:
    def test_happy_agent_model(self) -> None:
        inp = ConversionInput(
            agents={"build": {"model": "test/provider/model-a"}},
        )
        result = convert_legacy_to_native("oc-selector-test", inp)
        assert "agents" in result.profiles_entry
        assert result.profiles_entry["agents"]["build"]["models"] == ["test/provider/model-a"]

    def test_happy_agent_with_fallbacks(self) -> None:
        inp = ConversionInput(
            agents={"build": {"model": "p/m1", "fallback_models": ["p/m2", "p/m3"]}},
        )
        result = convert_legacy_to_native("oc-selector-test", inp)
        assert result.profiles_entry["agents"]["build"]["models"] == ["p/m1", "p/m2", "p/m3"]

    def test_happy_category_model(self) -> None:
        inp = ConversionInput(
            categories={"quick": {"model": "test/provider/quick"}},
        )
        result = convert_legacy_to_native("oc-selector-test", inp)
        assert result.profiles_entry["categories"]["quick"]["models"] == ["test/provider/quick"]

    def test_happy_disabled_hooks(self) -> None:
        inp = ConversionInput(disabled_hooks=["hook1", "hook2"])
        result = convert_legacy_to_native("oc-selector-test", inp)
        assert result.harness_entry["disabled_hooks"] == ["hook1", "hook2"]

    def test_happy_permission_under_harness(self) -> None:
        inp = ConversionInput(
            agents={"explore": {"model": "p/m", "permission": "allow"}},
        )
        result = convert_legacy_to_native("oc-selector-test", inp)
        assert HARNESS_KEY == "[opencode]"
        assert result.harness_entry["agents"]["explore"]["permission"] == "allow"

    def test_happy_multiple_agents(self) -> None:
        inp = ConversionInput(
            agents={
                "build": {"model": "p/build"},
                "explore": {"model": "p/explore"},
                "plan": {"model": "p/plan"},
            },
        )
        result = convert_legacy_to_native("oc-selector-test", inp)
        assert len(result.profiles_entry["agents"]) == 3

    def test_failure_unknown_agent_field(self) -> None:
        inp = ConversionInput(
            agents={"build": {"model": "p/m", "unknown_field": "x"}},
        )
        with pytest.raises(SchemaValidationError, match="Unsupported"):
            convert_legacy_to_native("oc-selector-test", inp)

    def test_failure_unknown_category_field(self) -> None:
        inp = ConversionInput(
            categories={"quick": {"model": "p/m", "bad": "x"}},
        )
        with pytest.raises(SchemaValidationError, match="Unsupported"):
            convert_legacy_to_native("oc-selector-test", inp)


class TestModernProjection:
    def test_happy_build_entry_with_model(self) -> None:
        entry = build_native_entry("oc-selector-test", model="p/m")
        assert entry["model"] == "p/m"

    def test_happy_build_entry_with_agents(self) -> None:
        entry = build_native_entry(
            "oc-selector-test",
            agents={"build": ["p/m1", "p/m2"]},
        )
        assert entry["agents"]["build"]["models"] == ["p/m1", "p/m2"]

    def test_happy_build_entry_with_categories(self) -> None:
        entry = build_native_entry(
            "oc-selector-test",
            categories={"quick": ["p/q"]},
        )
        assert entry["categories"]["quick"]["models"] == ["p/q"]

    def test_happy_empty_entry(self) -> None:
        entry = build_native_entry("oc-selector-test")
        assert entry == {}


class TestModelIdValidation:
    def test_happy_valid_model_id(self) -> None:
        assert validate_model_id("openai/gpt-4") == "openai/gpt-4"

    def test_happy_model_id_with_extra_slashes(self) -> None:
        assert validate_model_id("provider/model/sub-path") == "provider/model/sub-path"

    def test_failure_empty(self) -> None:
        with pytest.raises(SchemaValidationError):
            validate_model_id("")

    def test_failure_no_slash(self) -> None:
        with pytest.raises(SchemaValidationError, match="provider/model"):
            validate_model_id("noslash")

    def test_failure_whitespace(self) -> None:
        with pytest.raises(SchemaValidationError, match="whitespace"):
            validate_model_id("provider/model with space")

    def test_failure_empty_provider(self) -> None:
        with pytest.raises(SchemaValidationError):
            validate_model_id("/model")

    def test_failure_empty_model(self) -> None:
        with pytest.raises(SchemaValidationError):
            validate_model_id("provider/")
