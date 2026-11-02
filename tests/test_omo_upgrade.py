"""TDD tests for schema-validated exact OMO upgrade.

Covers:
- Validate config against schema
- Reject version mismatch
- Reject missing required fields
- Upgrade with new config
- Idempotent validation
"""

from __future__ import annotations

import json
from pathlib import Path  # noqa: TC003

import pytest
from pydantic import TypeAdapter

from oc_selector.errors import SchemaValidationError
from oc_selector.omo_upgrade import (
    OmoSchema,
    UpgradeResult,
    run_omo_upgrade,
    validate_omo_config,
)
from oc_selector.transactions import Transaction

_CONFIG_ADAPTER: TypeAdapter[dict[str, object]] = TypeAdapter(dict[str, object])


@pytest.fixture
def schema() -> OmoSchema:  # noqa: D103
    return OmoSchema(
        version="1.0.0",
        required_fields=("version", "name"),
        optional_fields=("description",),
    )


@pytest.fixture
def valid_config(tmp_path: Path) -> Path:  # noqa: D103
    config = {"version": "1.0.0", "name": "test-omo"}
    path = tmp_path / "omo.json"
    path.write_text(json.dumps(config))
    return path


@pytest.fixture
def block_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``Transaction.commit`` raise ``OSError`` to simulate a failed apply."""

    def _raise(
        self: Transaction,  # noqa: ARG001  # pyright: ignore[reportUnusedParameter]
    ) -> None:
        raise OSError("simulated commit failure")

    monkeypatch.setattr(Transaction, "commit", _raise)


class TestValidateOmoConfig:
    def test_happy_valid_config(self, valid_config: Path, schema: OmoSchema) -> None:
        data = validate_omo_config(valid_config, schema)
        assert data["version"] == "1.0.0"
        assert data["name"] == "test-omo"

    def test_failure_missing_file(self, tmp_path: Path, schema: OmoSchema) -> None:
        with pytest.raises(SchemaValidationError, match="not found"):
            validate_omo_config(tmp_path / "missing.json", schema)

    def test_failure_invalid_json(self, tmp_path: Path, schema: OmoSchema) -> None:
        path = tmp_path / "bad.json"
        path.write_text("not json")
        with pytest.raises(SchemaValidationError, match="Invalid JSON"):
            validate_omo_config(path, schema)

    def test_failure_missing_required(self, tmp_path: Path, schema: OmoSchema) -> None:
        path = tmp_path / "incomplete.json"
        path.write_text(json.dumps({"version": "1.0.0"}))
        with pytest.raises(SchemaValidationError, match="Missing required"):
            validate_omo_config(path, schema)

    def test_failure_version_mismatch(self, tmp_path: Path, schema: OmoSchema) -> None:
        path = tmp_path / "wrong_version.json"
        path.write_text(json.dumps({"version": "2.0.0", "name": "test"}))
        with pytest.raises(SchemaValidationError, match="Version mismatch"):
            validate_omo_config(path, schema)

    def test_failure_json_not_object(self, tmp_path: Path, schema: OmoSchema) -> None:
        """Validation rejects valid JSON that is not an object."""
        path = tmp_path / "array.json"
        path.write_text(json.dumps([1, 2]))
        with pytest.raises(
            SchemaValidationError,
            match="Config must be a JSON object",
        ):
            validate_omo_config(path, schema)


class TestRunOmoUpgrade:
    def test_happy_validate_only(
        self,
        valid_config: Path,
        schema: OmoSchema,
        tmp_path: Path,
    ) -> None:
        state_root = tmp_path / "state"
        state_root.mkdir()
        result = run_omo_upgrade(valid_config, schema, state_root)
        assert isinstance(result, UpgradeResult)
        assert result.success is True
        assert "already valid" in result.message

    def test_happy_upgrade_with_new_config(
        self,
        valid_config: Path,
        schema: OmoSchema,
        tmp_path: Path,
    ) -> None:
        state_root = tmp_path / "state"
        state_root.mkdir()
        new_config: dict[str, object] = {"version": "1.0.0", "name": "updated-omo"}
        result = run_omo_upgrade(valid_config, schema, state_root, new_config)
        assert result.success is True
        # Verify file was updated
        data = _CONFIG_ADAPTER.validate_python(json.loads(valid_config.read_text()))
        assert data["name"] == "updated-omo"

    def test_failure_invalid_new_config(
        self,
        valid_config: Path,
        schema: OmoSchema,
        tmp_path: Path,
    ) -> None:
        state_root = tmp_path / "state"
        state_root.mkdir()
        bad_config: dict[str, object] = {"version": "2.0.0", "name": "test"}
        result = run_omo_upgrade(valid_config, schema, state_root, bad_config)
        assert result.success is False
        assert "validation failed" in result.message

    def test_happy_idempotent_validation(
        self,
        valid_config: Path,
        schema: OmoSchema,
        tmp_path: Path,
    ) -> None:
        state_root = tmp_path / "state"
        state_root.mkdir()
        r1 = run_omo_upgrade(valid_config, schema, state_root)
        r2 = run_omo_upgrade(valid_config, schema, state_root)
        assert r1.success is True
        assert r2.success is True

    def test_failure_invalid_json_existing_config(
        self,
        tmp_path: Path,
        schema: OmoSchema,
    ) -> None:
        """Unparseable config yields from_version 'unknown' and 'Validation failed'."""
        config_path = tmp_path / "omo.json"
        config_path.write_text("not json")
        state_root = tmp_path / "state"
        state_root.mkdir()
        result = run_omo_upgrade(config_path, schema, state_root)
        assert result.success is False
        assert result.from_version == "unknown"
        assert result.to_version == "1.0.0"
        assert result.message == "Validation failed"
        assert result.error is not None
        assert "Invalid JSON" in result.error

    def test_failure_missing_config_file(
        self,
        tmp_path: Path,
        schema: OmoSchema,
    ) -> None:
        """Missing config yields from_version 'unknown' and 'Validation failed'."""
        state_root = tmp_path / "state"
        state_root.mkdir()
        result = run_omo_upgrade(tmp_path / "missing.json", schema, state_root)
        assert result.success is False
        assert result.from_version == "unknown"
        assert result.to_version == "1.0.0"
        assert result.message == "Validation failed"
        assert result.error is not None
        assert "not found" in result.error

    def test_failure_version_mismatch_existing_config(
        self,
        tmp_path: Path,
        schema: OmoSchema,
    ) -> None:
        """Version mismatch yields the actual from_version and 'Validation failed'."""
        config_path = tmp_path / "omo.json"
        config_path.write_text(json.dumps({"version": "2.0.0", "name": "test-omo"}))
        state_root = tmp_path / "state"
        state_root.mkdir()
        result = run_omo_upgrade(config_path, schema, state_root)
        assert result.success is False
        assert result.from_version == "2.0.0"
        assert result.to_version == "1.0.0"
        assert result.message == "Validation failed"
        assert result.error is not None
        assert "Version mismatch" in result.error

    def test_failure_commit_error_rolls_back(
        self,
        valid_config: Path,
        schema: OmoSchema,
        tmp_path: Path,
        block_commit: None,  # noqa: ARG002  # pyright: ignore[reportUnusedParameter]
    ) -> None:
        """Commit failure yields 'Upgrade failed' and leaves the config unchanged."""
        state_root = tmp_path / "state"
        state_root.mkdir()
        original = valid_config.read_text()
        new_config: dict[str, object] = {"version": "1.0.0", "name": "updated-omo"}
        result = run_omo_upgrade(valid_config, schema, state_root, new_config)
        assert result.success is False
        assert result.from_version == "1.0.0"
        assert result.to_version == "1.0.0"
        assert result.message == "Upgrade failed"
        assert result.error is not None
        assert valid_config.read_text() == original
