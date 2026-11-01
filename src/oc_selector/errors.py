"""Typed error hierarchy for oc-selector with public exit classifications.

Every error carries a machine-readable ``code`` for programmatic identification,
a public ``exit_code`` matching the status contract, and a human-facing
``message``. Exit codes:

- ``2``   argument / registry / boundary parse errors
- ``1``   maintenance / runtime wrapper failures
- ``127`` missing or unsupported executable
- ``126`` existing but non-executable file
- ``130`` interruption
"""

from __future__ import annotations

from typing import override


class OcSelectorError(Exception):
    """Base error carrying a public exit code and machine-readable code."""

    exit_code: int = 1
    code: str = "oc-selector-error"
    message: str

    def __init__(self, message: str) -> None:
        """Store the human-facing message."""
        super().__init__(message)
        self.message = message

    @override
    def __str__(self) -> str:
        """Return the human-facing message."""
        return self.message


class ArgumentError(OcSelectorError):
    """Argument or grammar error (exit 2)."""

    exit_code: int = 2
    code: str = "argument-error"


class RegistryError(OcSelectorError):
    """Registry parse or validation error (exit 2)."""

    exit_code: int = 2
    code: str = "registry-error"


class MaintenanceError(OcSelectorError):
    """Maintenance or runtime wrapper failure (exit 1)."""

    exit_code: int = 1
    code: str = "maintenance-error"


class ExecutableMissingError(OcSelectorError):
    """Required executable is missing (exit 127)."""

    exit_code: int = 127
    code: str = "executable-missing"


class ExecutableNotExecutableError(OcSelectorError):
    """Required executable exists but is not executable (exit 126)."""

    exit_code: int = 126
    code: str = "executable-not-executable"


class RecoveryError(OcSelectorError):
    """Recovery failure (exit 1)."""

    exit_code: int = 1
    code: str = "recovery-error"


class TransactionError(OcSelectorError):
    """Transaction failure (exit 1)."""

    exit_code: int = 1
    code: str = "transaction-error"


class SchemaValidationError(OcSelectorError):
    """Schema validation failure (exit 1)."""

    exit_code: int = 1
    code: str = "schema-validation-error"


class NetworkError(OcSelectorError):
    """Network failure (exit 1)."""

    exit_code: int = 1
    code: str = "network-error"


class InterruptionError(OcSelectorError):
    """Interruption (exit 130)."""

    exit_code: int = 130
    code: str = "interruption-error"
