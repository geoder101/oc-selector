"""TDD tests for comment-preserving JSONC adapter.

Covers:
- Roundtrip byte-identity
- Comment preservation on edit
- Trailing commas, Unicode, BOM
- Malformed input rejection
- Key navigation and value setting
"""

from __future__ import annotations

from pathlib import Path

import pytest

from oc_selector.errors import SchemaValidationError
from oc_selector.jsonc import JsoncAdapter

FIXTURES = Path(__file__).parent / "fixtures" / "jsonc"


@pytest.fixture
def adapter() -> JsoncAdapter:
    """Provide a fresh JsoncAdapter instance."""
    return JsoncAdapter()


class TestRoundtrip:
    def test_happy_basic_roundtrip(self, adapter: JsoncAdapter) -> None:
        text = (FIXTURES / "basic.jsonc").read_text()
        result = adapter.roundtrip(text)
        assert result == text

    def test_happy_trailing_comma_roundtrip(self, adapter: JsoncAdapter) -> None:
        text = (FIXTURES / "trailing_comma.jsonc").read_text()
        result = adapter.roundtrip(text)
        assert result == text

    def test_happy_unicode_roundtrip(self, adapter: JsoncAdapter) -> None:
        text = (FIXTURES / "unicode.jsonc").read_text()
        result = adapter.roundtrip(text)
        assert result == text

    def test_happy_plain_json_subset(self, adapter: JsoncAdapter) -> None:
        text = '{"a": 1, "b": [true, null]}\n'
        result = adapter.roundtrip(text)
        assert result == text


class TestCommentPreservation:
    def test_happy_line_comment_preserved_on_edit(self, adapter: JsoncAdapter) -> None:
        text = (FIXTURES / "basic.jsonc").read_text()
        model = adapter.load(text)
        model_node = adapter.get_value(model, "agents", "build", "model")
        model_node.raw_value = '"new/model"'  # pyright: ignore[reportAttributeAccessIssue]
        model_node.value = "new/model"  # pyright: ignore[reportAttributeAccessIssue]
        output = adapter.dump(model)
        assert "// Main config" in output
        assert "/* Block comment for categories */" in output
        assert '"new/model"' in output

    def test_happy_unrelated_bytes_unchanged(self, adapter: JsoncAdapter) -> None:
        text = (FIXTURES / "basic.jsonc").read_text()
        model = adapter.load(text)
        # Edit one value
        model_node = adapter.get_value(model, "agents", "explore", "model")
        model_node.raw_value = '"changed/model"'  # pyright: ignore[reportAttributeAccessIssue]
        model_node.value = "changed/model"  # pyright: ignore[reportAttributeAccessIssue]
        output = adapter.dump(model)
        # Other values unchanged
        assert '"test/provider/build-model"' in output
        assert '"test/provider/quick-model"' in output
        # Comments still there
        assert "// Main config" in output


class TestMalformed:
    def test_failure_malformed_jsonc(self, adapter: JsoncAdapter) -> None:
        text = (FIXTURES / "malformed.jsonc").read_text()
        with pytest.raises(SchemaValidationError, match="parse failed"):
            adapter.load(text)

    def test_failure_empty_input(self, adapter: JsoncAdapter) -> None:
        with pytest.raises(SchemaValidationError):
            adapter.load("")

    def test_failure_truncated(self, adapter: JsoncAdapter) -> None:
        with pytest.raises(SchemaValidationError):
            adapter.load('{"a": ')


class TestKeyNavigation:
    def test_happy_get_nested_value(self, adapter: JsoncAdapter) -> None:
        text = (FIXTURES / "basic.jsonc").read_text()
        model = adapter.load(text)
        node = adapter.get_value(model, "agents", "build", "model")
        assert node.raw_value == '"test/provider/build-model"'  # pyright: ignore[reportAttributeAccessIssue,reportUnknownMemberType]

    def test_happy_has_key(self, adapter: JsoncAdapter) -> None:
        text = (FIXTURES / "basic.jsonc").read_text()
        model = adapter.load(text)
        assert adapter.has_key(model, "agents", "build")
        assert not adapter.has_key(model, "agents", "nonexistent")

    def test_failure_missing_key(self, adapter: JsoncAdapter) -> None:
        text = (FIXTURES / "basic.jsonc").read_text()
        model = adapter.load(text)
        with pytest.raises(SchemaValidationError, match="not found"):
            adapter.get_value(model, "nonexistent")

    def test_failure_wrong_type(self, adapter: JsoncAdapter) -> None:
        text = (FIXTURES / "basic.jsonc").read_text()
        model = adapter.load(text)
        # "model" is a string, not an object
        with pytest.raises(SchemaValidationError):
            adapter.get_value(model, "model", "subkey")


class TestSetValue:
    def test_happy_set_string_value(self, adapter: JsoncAdapter) -> None:
        text = (FIXTURES / "basic.jsonc").read_text()
        model = adapter.load(text)
        adapter.set_value(model, "agents", "build", "model", '"new/model"')
        output = adapter.dump(model)
        assert '"new/model"' in output
        # Comments preserved
        assert "// Main config" in output

    def test_failure_set_value_no_args(self, adapter: JsoncAdapter) -> None:
        """set_value with no arguments raises a schema validation error."""
        model = adapter.load('{"a": 1}')
        with pytest.raises(SchemaValidationError, match="at least key and value"):
            adapter.set_value(model)

    def test_failure_set_value_single_arg(self, adapter: JsoncAdapter) -> None:
        """set_value with only a key and no value raises a schema validation error."""
        model = adapter.load('{"a": 1}')
        with pytest.raises(SchemaValidationError, match="at least key and value"):
            adapter.set_value(model, "onlykey")

    def test_failure_set_value_no_raw_value(self, adapter: JsoncAdapter) -> None:
        """set_value on a boolean target (no raw_value) raises a schema error."""
        model = adapter.load('{"a": true}')
        with pytest.raises(SchemaValidationError, match="does not support raw_value"):
            adapter.set_value(model, "a", '"x"')

    def test_happy_set_value_integer_target(self, adapter: JsoncAdapter) -> None:
        """set_value on an integer target updates raw_value and value."""
        model = adapter.load('{"a": 1}')
        adapter.set_value(model, "a", '"new"')
        output = adapter.dump(model)
        assert output == '{"a": "new"}'


class TestToPlain:
    def test_happy_to_plain_nested(self, adapter: JsoncAdapter) -> None:
        """to_plain converts a model to a plain dict with nested values."""
        model = adapter.load('{"a": 1, "b": {"c": "x"}}')
        plain = adapter.to_plain(model)
        assert plain == {"a": 1, "b": {"c": "x"}}
