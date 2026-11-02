"""Comment-preserving JSONC adapter using json-five ModelLoader/ModelDumper.

Contract:
- JSONC uses json-five ModelLoader/ModelDumper through one adapter.
- Roundtrip unchanged documents byte-identically.
- Edited documents preserve unrelated comments, indentation, ordering and values.
- Reject duplicate relevant keys, malformed JSONC, unsupported model nodes.
- Plain JSON is a valid subset, not a second runtime parser.
- The adapter transforms bytes; file I/O is handled by transactions.
"""

from __future__ import annotations

from typing import ClassVar

from json5 import dumper, loader
from json5.model import (
    JSONObject,
    JSONText,
    Node,
)
from pydantic import BaseModel, ConfigDict, TypeAdapter

from oc_selector.errors import SchemaValidationError


class _JsonTextWrapper(BaseModel):
    """Arbitrary-type wrapper validating a json-five node by identity.

    ``JSONText`` has no Pydantic core schema, so the loader result is
    validated through this wrapper (an ``isinstance`` check under the hood).
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(arbitrary_types_allowed=True)

    value: JSONText


_PLAIN_DICT_ADAPTER: TypeAdapter[dict[str, object]] = TypeAdapter(dict[str, object])


class JsoncAdapter:
    """Typed facade over json-five for comment-preserving JSONC edits."""

    def load(self, text: str) -> JSONText:
        """Parse JSONC text into a json-five model.

        Raises ``SchemaValidationError`` on malformed input.
        """
        try:
            ml = loader.ModelLoader()
            wrapped = _JsonTextWrapper.model_validate(
                {"value": loader.loads(text, loader=ml)},
            )
        except Exception as e:
            raise SchemaValidationError(f"JSONC parse failed: {e}") from e
        return wrapped.value

    def dump(self, model: JSONText, indent: int = 4) -> str:
        """Serialize a json-five model back to JSONC text."""
        md = dumper.ModelDumper()
        return dumper.dumps(model, dumper=md, indent=indent)  # pyright: ignore[reportArgumentType]

    def roundtrip(self, text: str) -> str:
        """Parse and re-serialize; must be byte-identical for valid input."""
        model = self.load(text)
        return self.dump(model)

    def get_value(self, model: JSONText, *path: str) -> Node:
        """Navigate to a value by key path. Raises on missing key."""
        current: Node = model
        for key in path:
            current = self._get_key(current, key)
        return current

    def set_value(self, model: JSONText, *path_and_value: str) -> None:
        """Set a value at a key path. Last element is the new string value."""
        min_args = 2
        if len(path_and_value) < min_args:
            raise SchemaValidationError("set_value requires at least key and value")
        keys = path_and_value[:-1]
        new_value = path_and_value[-1]
        current: Node = model
        for key in keys:
            current = self._get_key(current, key)
        if not hasattr(current, "raw_value"):
            raise SchemaValidationError("Target node does not support raw_value edit")
        current.raw_value = new_value  # pyright: ignore[reportAttributeAccessIssue]
        if hasattr(current, "value"):
            current.value = new_value.strip('"')  # pyright: ignore[reportAttributeAccessIssue]

    def _get_key(self, node: Node, key: str) -> Node:
        """Get a child node by key from a JSONObject or JSONText."""
        obj = node.value if isinstance(node, JSONText) else node
        if not isinstance(obj, JSONObject):
            raise SchemaValidationError(
                f"Expected object at key {key!r}, got {type(obj).__name__}",
            )
        for i, k in enumerate(obj.keys):
            if k.characters == key:  # pyright: ignore[reportAttributeAccessIssue,reportUnknownMemberType]
                return obj.values[i]
        raise SchemaValidationError(f"Key {key!r} not found")

    def has_key(self, model: JSONText, *path: str) -> bool:
        """Check if a key path exists without raising."""
        try:
            self.get_value(model, *path)
        except SchemaValidationError:
            return False
        return True

    def to_plain(self, model: JSONText) -> dict[str, object]:
        """Convert model to plain Python dict (loses comments)."""
        ml = loader.DefaultLoader()
        return _PLAIN_DICT_ADAPTER.validate_python(
            loader.loads(self.dump(model), loader=ml),
        )
