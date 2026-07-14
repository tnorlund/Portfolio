"""Contract tests for the entity-wide DynamoDB serialization mixin."""

# pylint: disable=protected-access

from datetime import datetime, timezone

import pytest

from receipt_dynamo.entities.entity_mixins import SerializationMixin

pytestmark = pytest.mark.unit


@pytest.fixture(name="serializer")
def fixture_serializer():
    return SerializationMixin()


@pytest.mark.parametrize(
    "value",
    [
        "receipt",
        True,
        False,
        -7,
        0.75,
        None,
        b"data",
        [],
        ["merchant", "merchant", "total"],
        [True, 1, {"nested": ["value"]}],
        {"flag": False, "items": [1, 2]},
        {"merchant", "total"},
        {-4, 0.5},
        {b"merchant", b"total"},
    ],
)
def test_mixin_round_trip_preserves_supported_value_types(serializer, value):
    encoded = serializer._python_to_dynamo(value)
    restored = serializer._dynamo_to_python(encoded)

    assert restored == value
    assert restored.__class__ is value.__class__


def test_string_lists_remain_ordered_lists(serializer):
    value = ["total", "merchant", "total"]

    encoded = serializer._serialize_value(value)

    assert encoded == {
        "L": [{"S": "total"}, {"S": "merchant"}, {"S": "total"}]
    }
    assert serializer._dynamo_to_python(encoded) == value


@pytest.mark.parametrize(
    ("value", "marker"),
    [
        (True, "BOOL"),
        (1, "N"),
        (b"bytes", "B"),
        ({"string"}, "SS"),
        ({1, 2.5}, "NS"),
        ({b"a", b"b"}, "BS"),
    ],
)
def test_mixin_uses_dynamodb_type_markers(serializer, value, marker):
    assert set(serializer._serialize_value(value)) == {marker}


@pytest.mark.parametrize(
    "value", [set(), {True}, {True, 2}, {""}, {b""}, {"one", 2}]
)
def test_mixin_rejects_invalid_sets(serializer, value):
    with pytest.raises((TypeError, ValueError)):
        serializer._serialize_value(value)


def test_datetime_keeps_the_existing_iso_string_contract(serializer):
    value = datetime(2026, 7, 14, 12, 30, tzinfo=timezone.utc)

    assert serializer._serialize_value(value) == {"S": value.isoformat()}


def test_empty_strings_keep_the_existing_null_contract(serializer):
    assert serializer._serialize_value("") == {"NULL": True}
    assert serializer._dynamo_to_python({"NULL": True}) is None


def test_unsupported_objects_keep_the_existing_string_fallback(serializer):
    class CustomValue:
        def __str__(self):
            return "custom-value"

    assert serializer._serialize_value(CustomValue()) == {"S": "custom-value"}


@pytest.mark.parametrize(
    ("field_type", "encoded", "expected"),
    [
        (list, {"L": [{"S": "a"}, {"S": "a"}]}, ["a", "a"]),
        (set, {"SS": ["a", "b"]}, {"a", "b"}),
        (set, {"NS": ["-2", "0.5"]}, {-2, 0.5}),
        (set, {"BS": [b"a", b"b"]}, {b"a", b"b"}),
    ],
)
def test_safe_deserialize_field_validates_collection_type(
    field_type, encoded, expected
):
    assert (
        SerializationMixin.safe_deserialize_field(
            {"value": encoded}, "value", field_type=field_type
        )
        == expected
    )


@pytest.mark.parametrize(
    ("item", "default", "field_type"),
    [
        ({}, "default", str),
        ({"value": {"NULL": True}}, "default", str),
        ({"value": {"N": "1"}}, "default", str),
        ({"value": {"N": "not-a-number"}}, "default", int),
    ],
)
def test_safe_deserialize_field_returns_default_for_unusable_values(
    item, default, field_type
):
    assert (
        SerializationMixin.safe_deserialize_field(
            item, "value", default=default, field_type=field_type
        )
        == default
    )


def test_build_dynamodb_item_combines_keys_indexes_and_overrides():
    class Record(SerializationMixin):
        def __init__(self):
            self.name = "auto"
            self.excluded = "hidden"

        @property
        def key(self):
            return {"PK": {"S": "RECORD#1"}, "SK": {"S": "RECORD"}}

        def gsi1_key(self):
            return {"GSI1PK": {"S": "TYPE#RECORD"}}

    item = Record().build_dynamodb_item(
        "RECORD",
        gsi_methods=["gsi1_key", "missing_index"],
        custom_fields={"name": {"S": "custom"}},
        exclude_fields={"excluded"},
    )

    assert item == {
        "TYPE": {"S": "RECORD"},
        "PK": {"S": "RECORD#1"},
        "SK": {"S": "RECORD"},
        "GSI1PK": {"S": "TYPE#RECORD"},
        "name": {"S": "custom"},
    }


def test_required_keys_and_key_component_extraction():
    item = {"PK": {"S": "IMAGE#abc"}, "SK": {"S": "RECEIPT#3"}}

    SerializationMixin.validate_required_keys(item, {"PK", "SK"})
    components = SerializationMixin.extract_key_components(
        item, "IMAGE#", "RECEIPT#"
    )

    assert components["pk_parts"] == ["IMAGE", "abc"]
    assert components["sk_parts"] == ["RECEIPT", "3"]

    with pytest.raises(ValueError, match="missing required keys"):
        SerializationMixin.validate_required_keys(item, {"TYPE"})
    with pytest.raises(ValueError, match="Invalid PK format"):
        SerializationMixin.extract_key_components(item, "JOB#", "RECEIPT#")
    with pytest.raises(ValueError, match="Invalid SK format"):
        SerializationMixin.extract_key_components(item, "IMAGE#", "WORD#")
    with pytest.raises(ValueError, match="Error parsing key components"):
        SerializationMixin.extract_key_components({}, "IMAGE#", "WORD#")
