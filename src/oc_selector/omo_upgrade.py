"""Schema-validated exact OMO upgrade.

OMO upgrade is schema-validated before any mutation.
No drift - exact version pinning.
No author constants in installed src.
No silent fallbacks.
No network in validation (only local file operations).
No secret material in logs.
No migration of legacy fields.
All mutations go through transaction.
Idempotent operations.
No conversion calls.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path  # noqa: TC003

from pydantic import TypeAdapter, ValidationError

from oc_selector.errors import (
    MaintenanceError,
    SchemaValidationError,
)
from oc_selector.transactions import Transaction

# Normalizes parsed JSON values to plain typed dicts at the OMO config boundary.
_PLAIN_DICT_ADAPTER: TypeAdapter[dict[str, object]] = TypeAdapter(dict[str, object])


@dataclass(frozen=True, slots=True)
class OmoSchema:
    """Typed OMO schema for validation."""

    version: str
    required_fields: tuple[str, ...]
    optional_fields: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class UpgradeResult:
    """Result of an OMO upgrade operation."""

    success: bool
    from_version: str
    to_version: str
    message: str
    error: str | None = None


def _validate_config_dict(data: dict[str, object], schema: OmoSchema) -> str | None:
    """Return an error message if ``data`` violates ``schema``, else ``None``."""
    for field in schema.required_fields:
        if field not in data:
            return f"Missing required field: {field}"
    version = data.get("version")
    if version != schema.version:
        return f"Version mismatch: expected {schema.version}, got {version}"
    return None


def validate_omo_config(
    config_path: Path,
    schema: OmoSchema,
) -> dict[str, object]:
    """Validate an OMO config file against the schema.

    Args:
        config_path: Path to the OMO config file.
        schema: The schema to validate against.

    Returns:
        The parsed config as a dict.

    Raises:
        ``SchemaValidationError`` on validation failure.

    """
    if not config_path.exists():
        raise SchemaValidationError(f"Config not found: {config_path}")

    try:
        data = _PLAIN_DICT_ADAPTER.validate_python(json.loads(config_path.read_text()))
    except json.JSONDecodeError as exc:
        raise SchemaValidationError(f"Invalid JSON: {exc}") from exc
    except ValidationError:
        raise SchemaValidationError("Config must be a JSON object") from None

    problem = _validate_config_dict(data, schema)
    if problem is not None:
        raise SchemaValidationError(problem)

    return data


def run_omo_upgrade(
    config_path: Path,
    schema: OmoSchema,
    state_root: Path,
    new_config: dict[str, object] | None = None,
    *,
    dry_run: bool = False,
) -> UpgradeResult:
    """Run a schema-validated OMO upgrade.

    Args:
        config_path: Path to the OMO config file.
        schema: The target schema.
        state_root: Path to the state root.
        new_config: New config content (None for in-place validation).
        dry_run: With a ``new_config``, validate and report without writing.

    Returns:
        An ``UpgradeResult`` describing the outcome.

    """
    # Read current version
    current_version = "unknown"
    if config_path.exists():
        try:
            current_data = _PLAIN_DICT_ADAPTER.validate_python(
                json.loads(config_path.read_text()),
            )
            current_version = str(current_data.get("version", "unknown"))
        except json.JSONDecodeError, OSError, ValidationError:
            current_version = "unknown"

    # If no new config, just validate existing
    if new_config is None:
        try:
            validate_omo_config(config_path, schema)
            return UpgradeResult(
                success=True,
                from_version=current_version,
                to_version=schema.version,
                message="Config already valid",
            )
        except SchemaValidationError as exc:
            return UpgradeResult(
                success=False,
                from_version=current_version,
                to_version=schema.version,
                message="Validation failed",
                error=str(exc),
            )

    if dry_run:
        problem = _validate_config_dict(new_config, schema)
        will_upgrade = problem is None
        message = (
            f"Dry run: would upgrade from {current_version} to {schema.version}"
            if will_upgrade
            else "New config validation failed"
        )
        return UpgradeResult(
            success=will_upgrade,
            from_version=current_version,
            to_version=schema.version,
            message=message,
            error=problem,
        )

    # Validate new config against schema
    try:
        # Write to temp for validation
        tmp_config = state_root / "staging" / "new_omo_config.json"
        tmp_config.parent.mkdir(parents=True, exist_ok=True)
        tmp_config.write_text(json.dumps(new_config))
        validate_omo_config(tmp_config, schema)
    except SchemaValidationError as exc:
        return UpgradeResult(
            success=False,
            from_version=current_version,
            to_version=schema.version,
            message="New config validation failed",
            error=str(exc),
        )

    # Use transaction for mutation
    tx = Transaction(state_root, operation="omo-upgrade")
    try:
        tx.begin()
        tmp_file = state_root / "staging" / tx.id / "new_omo_config.json"
        tmp_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_file.write_text(json.dumps(new_config))
        tx.stage_file(tmp_file, config_path)
        tx.commit()
    except (OSError, MaintenanceError) as exc:
        tx.rollback()
        return UpgradeResult(
            success=False,
            from_version=current_version,
            to_version=schema.version,
            message="Upgrade failed",
            error=str(exc),
        )
    finally:
        tx.close()

    return UpgradeResult(
        success=True,
        from_version=current_version,
        to_version=schema.version,
        message=f"Upgraded from {current_version} to {schema.version}",
    )
