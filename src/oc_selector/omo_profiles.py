"""Parameterized legacy conversion and modern native projections.

Conversion contract:

- Installed conversion helpers accept typed model IDs, profile/native names,
  plugin pins and field policies as parameters.
- No author model/provider/name defaults or recipe resources in installed src.
- Generic init never calls conversion.
- Translate model/fallback lists into native ``profiles.<owned>.agents/categories``.
- Put ``disabled_hooks`` and harness-specific agent permission under
  the literal JSON key ``profiles[owned]["[opencode]"]``.
- Normalize supplied chains deterministically into native ``models`` arrays.
- Preserve provider/reference text without opening secret targets.
- Unknown unsupported fields fail before commit, not silently discarded.

Modern projection:

- Separate from legacy conversion.
- Singleton models across covered existing supported roster.
- Preserve permissions/hooks/non-models.
- Unknown selectors/schema/legacy migration refuse customization only.
- KEEP remains possible without resolution.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import TypeAdapter

from oc_selector.errors import SchemaValidationError

# Typed boundary for legacy ``fallback_models`` values narrowed to ``list``.
_OBJECT_LIST_ADAPTER: TypeAdapter[list[object]] = TypeAdapter(list[object])

# Core agent names that receive core overrides
CORE_AGENT_NAMES: frozenset[str] = frozenset(
    {"build", "general", "explore", "plan", "compaction", "title", "summary"},
)

# The literal harness key (square brackets are part of the key)
HARNESS_KEY = "[opencode]"


@dataclass(frozen=True, slots=True)
class ConversionInput:
    """Typed input for legacy-to-native conversion."""

    agents: dict[str, dict[str, object]] = field(default_factory=dict)
    categories: dict[str, dict[str, object]] = field(default_factory=dict)
    disabled_hooks: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ConversionResult:
    """Typed output of legacy-to-native conversion."""

    profiles_entry: dict[str, object] = field(default_factory=dict)
    harness_entry: dict[str, object] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _build_models_list(fields: dict[str, object]) -> list[object] | None:
    """Build the native ``models`` list from legacy model/fallback fields.

    Returns None when neither ``model`` nor ``fallback_models`` is present,
    so the caller omits the ``models`` key entirely. An explicit
    ``fallback_models: null`` is appended as ``None`` (legacy behavior).
    """
    if "model" not in fields and "fallback_models" not in fields:
        return None
    models: list[object] = []
    if "model" in fields:
        models.append(fields["model"])
    if "fallback_models" in fields:
        fallbacks = fields["fallback_models"]
        if isinstance(fallbacks, list):
            models.extend(_OBJECT_LIST_ADAPTER.validate_python(fallbacks))
        else:
            models.append(fallbacks)
    return models


def convert_legacy_to_native(  # noqa: C901, PLR0912
    owned_name: str,  # noqa: ARG001  # pyright: ignore[reportUnusedParameter]
    input_data: ConversionInput,
) -> ConversionResult:
    """Convert legacy OMO fields to native profile entry structure.

    Args:
        owned_name: The owned profile name (e.g. "oc-selector-gpt").
        input_data: Typed legacy input fields.

    Returns:
        A ``ConversionResult`` with the native entry and harness entry.

    Raises:
        ``SchemaValidationError`` on unknown/unsupported fields.

    """
    warnings: list[str] = []
    agents_out: dict[str, object] = {}
    categories_out: dict[str, object] = {}
    harness_agents: dict[str, object] = {}

    # Convert agents
    for agent_name, agent_fields in input_data.agents.items():
        agent_entry: dict[str, object] = {}
        models = _build_models_list(agent_fields)
        if models is not None:
            agent_entry["models"] = models
        # Permission goes under harness key
        if "permission" in agent_fields:
            harness_agents[agent_name] = {"permission": agent_fields["permission"]}
        # Unknown fields
        known = {"model", "fallback_models", "permission"}
        for k in agent_fields:
            if k not in known:
                raise SchemaValidationError(
                    f"Unsupported agent field {k!r} in agent {agent_name!r}",
                )
        if agent_entry:
            agents_out[agent_name] = agent_entry

    # Convert categories
    for cat_name, cat_fields in input_data.categories.items():
        cat_entry: dict[str, object] = {}
        models = _build_models_list(cat_fields)
        if models is not None:
            cat_entry["models"] = models
        known = {"model", "fallback_models"}
        for k in cat_fields:
            if k not in known:
                raise SchemaValidationError(
                    f"Unsupported category field {k!r} in category {cat_name!r}",
                )
        if cat_entry:
            categories_out[cat_name] = cat_entry

    # Build the native profile entry
    profile_entry: dict[str, object] = {}
    if agents_out:
        profile_entry["agents"] = agents_out
    if categories_out:
        profile_entry["categories"] = categories_out

    # Build the harness entry
    harness: dict[str, object] = {}
    if harness_agents:
        harness["agents"] = harness_agents
    if input_data.disabled_hooks:
        harness["disabled_hooks"] = input_data.disabled_hooks

    return ConversionResult(
        profiles_entry=profile_entry,
        harness_entry=harness,
        warnings=warnings,
    )


def build_native_entry(
    owned_name: str,  # noqa: ARG001  # pyright: ignore[reportUnusedParameter]
    model: str | None = None,
    agents: dict[str, list[str]] | None = None,
    categories: dict[str, list[str]] | None = None,
) -> dict[str, object]:
    """Build a modern native profile entry.

    Args:
        owned_name: The owned profile name.
        model: Top-level model (singleton).
        agents: Agent name to models list mapping.
        categories: Category name to models list mapping.

    Returns:
        A dict representing the native profile entry.

    """
    entry: dict[str, object] = {}
    if model is not None:
        entry["model"] = model
    if agents:
        entry["agents"] = {name: {"models": models} for name, models in agents.items()}
    if categories:
        entry["categories"] = {
            name: {"models": models} for name, models in categories.items()
        }
    return entry


def validate_model_id(model_id: str) -> str:
    """Validate a model ID format (provider/model with optional slashes).

    Model IDs split on first slash into nonempty provider/model.
    Allow additional model slashes. Reject whitespace/control characters.
    """
    if not model_id:
        raise SchemaValidationError("Model ID is empty")
    control_char_limit = 32
    min_parts = 2
    if any(c.isspace() or ord(c) < control_char_limit for c in model_id):
        raise SchemaValidationError(
            f"Model ID contains whitespace/control: {model_id!r}",
        )
    parts = model_id.split("/", 1)
    if len(parts) < min_parts or not parts[0] or not parts[1]:
        raise SchemaValidationError(
            f"Model ID must be provider/model format: {model_id!r}",
        )
    return model_id
