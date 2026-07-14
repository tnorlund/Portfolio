"""Contract tests for shared DynamoDB attribute conversion utilities."""

import math
from decimal import Decimal

import pytest

from receipt_dynamo.entities.dynamodb_utils import (
    dict_to_dynamodb_map,
    parse_dynamodb_map,
    parse_dynamodb_value,
    to_dynamodb_value,
    validate_required_keys,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "value",
    [
        "",
        "receipt",
        True,
        False,
        0,
        -42,
        0.125,
        None,
        b"receipt-bytes",
        [],
        ["duplicate", "duplicate"],
        [True, 1, "one", b"one", None],
        {"nested": {"flags": [True, False], "count": -2}},
        {"merchant", "receipt"},
        {-2, 0, 1.5},
        {b"merchant", b"receipt"},
    ],
)
def test_attribute_values_round_trip_without_changing_python_type(value):
    """Every supported attribute keeps its collection and scalar types."""
    restored = parse_dynamodb_value(to_dynamodb_value(value))

    assert restored == value
    assert restored.__class__ is value.__class__


def test_nested_map_round_trip_preserves_lists_sets_and_bool_values():
    """Recursive conversion uses the same type dispatch at every depth."""
    value = {
        "records": [
            {
                "enabled": True,
                "labels": ["merchant", "total"],
                "scores": {-1, 0.5},
            }
        ],
        "binary": {b"a", b"b"},
    }

    encoded = dict_to_dynamodb_map(value)
    restored = parse_dynamodb_map(encoded)

    assert encoded["records"]["L"][0]["M"]["enabled"] == {"BOOL": True}
    assert encoded["records"]["L"][0]["M"]["labels"] == {
        "L": [{"S": "merchant"}, {"S": "total"}]
    }
    assert restored == value
    assert isinstance(restored["records"], list)
    assert isinstance(restored["records"][0]["scores"], set)


def test_decimal_values_use_number_attributes_in_nested_collections():
    """Decimals remain numbers on the wire, including recursive values."""
    value = {
        "amount": Decimal("10.25"),
        "items": [Decimal("1"), {"tax": Decimal("0.75")}],
        "totals": {Decimal("2.5"), Decimal("3")},
    }

    encoded = dict_to_dynamodb_map(value)

    assert encoded["amount"] == {"N": "10.25"}
    assert encoded["items"]["L"][0] == {"N": "1"}
    assert encoded["items"]["L"][1]["M"]["tax"] == {"N": "0.75"}
    assert encoded["totals"] == {"NS": ["2.5", "3"]}

    # DynamoDB's N marker does not retain whether the caller supplied a float
    # or Decimal. The established parser intentionally restores fractional N
    # values as float and integral N values as int.
    assert parse_dynamodb_map(encoded) == {
        "amount": 10.25,
        "items": [1, {"tax": 0.75}],
        "totals": {2.5, 3},
    }


@pytest.mark.parametrize("value", [True, False])
def test_bool_dispatch_precedes_integer_dispatch(value):
    assert to_dynamodb_value(value) == {"BOOL": value}


def test_negative_integer_number_sets_deserialize_as_integers():
    restored = parse_dynamodb_value({"NS": ["-7", "3", "0.25"]})

    assert restored == {-7, 3, 0.25}
    assert any(value.__class__ is int and value == -7 for value in restored)


@pytest.mark.parametrize(
    ("value", "error", "message"),
    [
        (set(), ValueError, "cannot be empty"),
        ({True}, TypeError, "cannot contain bool"),
        ({True, 2}, TypeError, "cannot contain bool"),
        ({""}, ValueError, "cannot contain empty"),
        ({b""}, ValueError, "cannot contain empty"),
        ({"receipt", 1}, TypeError, "must contain only"),
        ({b"receipt", "receipt"}, TypeError, "must contain only"),
    ],
)
def test_invalid_dynamodb_sets_are_rejected(value, error, message):
    """Unsupported sets fail instead of being silently stringified."""
    with pytest.raises(error, match=message):
        to_dynamodb_value(value)


@pytest.mark.parametrize(
    "value",
    [
        math.inf,
        -math.inf,
        math.nan,
        Decimal("Infinity"),
        Decimal("-Infinity"),
        Decimal("NaN"),
    ],
)
def test_non_finite_numbers_are_rejected(value):
    with pytest.raises(ValueError, match="must be finite"):
        to_dynamodb_value(value)


@pytest.mark.parametrize(
    "value",
    [
        {math.inf},
        {-math.inf},
        {math.nan},
        {Decimal("Infinity")},
        {Decimal("-Infinity")},
        {Decimal("NaN")},
    ],
)
def test_non_finite_number_sets_are_rejected(value):
    with pytest.raises(ValueError, match="must be finite"):
        to_dynamodb_value(value)


def test_unsupported_objects_keep_the_documented_string_fallback():
    class CustomValue:
        def __str__(self):
            return "custom-value"

    assert to_dynamodb_value(CustomValue()) == {"S": "custom-value"}


def test_unknown_dynamodb_marker_returns_none():
    assert parse_dynamodb_value({"UNKNOWN": "value"}) is None


def test_required_key_validation_reports_missing_and_additional_keys():
    validate_required_keys({"PK": {}, "SK": {}}, {"PK", "SK"}, "record")

    with pytest.raises(ValueError, match="Invalid record format") as error:
        validate_required_keys({"PK": {}, "extra": {}}, {"PK", "SK"}, "record")

    assert "'SK'" in str(error.value)
    assert "'extra'" in str(error.value)
