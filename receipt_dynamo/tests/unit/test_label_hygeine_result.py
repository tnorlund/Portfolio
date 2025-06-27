from datetime import datetime

import pytest

from receipt_dynamo.entities.label_hygiene_result import (
    LabelHygieneResult,
    item_to_label_hygiene_result,
)

# === FIXTURE ===


@pytest.fixture
def example_label_hygiene_result():
    return LabelHygieneResult(
        hygiene_id="de305d54-75b4-431b-adb2-eb6b9e546014",
        alias="SUB_TOTAL",
        canonical_label="SUBTOTAL",
        reasoning="Common alias used in historical labels.",
        gpt_agreed=True,
        source_batch_id="batch-xyz",
        example_ids=["RECEIPT#1#LINE#2#WORD#3"],
        timestamp=datetime(2024, 1, 1, 12, 0, 0),
        image_id="b98df7e6-43f5-43ad-9ee3-e39070c7d9df",
        receipt_id=101,
    )


# === CONSTRUCTOR & FIELD VALIDATION ===


@pytest.mark.unit
@pytest.mark.parametrize(
    "field,value,error",
    [
        ("alias", 123, "alias must be a string"),
        ("canonical_label", 456, "canonical_label must be a string"),
        ("reasoning", 789, "reasoning must be a string"),
        ("gpt_agreed", "yes", "gpt_agreed must be a boolean"),
        ("source_batch_id", False, "source_batch_id must be a string or None"),
        ("example_ids", 123, "example_ids must be a list"),
        ("image_id", 123, "image_id must be a string"),
        ("receipt_id", "101", "receipt_id must be an integer"),
        ("timestamp", "not-a-datetime", "timestamp must be a datetime object"),
    ],
)
def test_label_hygiene_result_constructor_type_validation(field, value, error):
    kwargs = dict(
        hygiene_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        alias="SUB_TOTAL",
        canonical_label="SUBTOTAL",
        reasoning="Reason",
        gpt_agreed=True,
        source_batch_id="batch-xyz",
        example_ids=["a"],
        timestamp=datetime.now(),
        image_id="img",
        receipt_id=101,
    )
    kwargs[field] = value
    with pytest.raises(ValueError, match=error):
        LabelHygieneResult(**kwargs)


# === SERIALIZATION / DESERIALIZATION ===


@pytest.mark.unit
def test_label_hygiene_result_roundtrip(example_label_hygiene_result):
    item = example_label_hygiene_result.to_item()
    restored = item_to_label_hygiene_result(item)
    assert restored == example_label_hygiene_result


@pytest.mark.unit
def test_label_hygiene_result_missing_keys():
    item = {
        "PK": {"S": "LABEL_HYGIENE#a1b2c3d4-e5f6-7890-1234-56789abcdef0"},
        "SK": {"S": "FROM#SUB_TOTAL#TO#SUBTOTAL"},
        "TYPE": {"S": "LABEL_HYGIENE_RESULT"},
        "alias": {"S": "SUB_TOTAL"},
        "canonical_label": {"S": "SUBTOTAL"},
        # missing keys like reasoning, gpt_agreed, timestamp
    }
    with pytest.raises(ValueError, match="missing keys"):
        item_to_label_hygiene_result(item)


def test_label_hygiene_result_example_ids_not_list():
    with pytest.raises(ValueError, match="example_ids must be a list"):
        LabelHygieneResult(
            hygiene_id="de305d54-75b4-431b-adb2-eb6b9e546014",
            alias="SUB_TOTAL",
            canonical_label="SUBTOTAL",
            reasoning="Invalid example_ids",
            gpt_agreed=True,
            source_batch_id="batch-xyz",
            example_ids="not-a-list",  # ❌ not a list
            timestamp=datetime.now(),
            image_id="b98df7e6-43f5-43ad-9ee3-e39070c7d9df",
            receipt_id=101,
        )


@pytest.mark.unit
def test_label_hygiene_result_malformed_pk_raises():
    item = {
        "PK": {"S": "LABEL_HYGIENE"},  # malformed
        "SK": {"S": "FROM#A#TO#B"},
        "TYPE": {"S": "LABEL_HYGIENE_RESULT"},
        "alias": {"S": "A"},
        "canonical_label": {"S": "B"},
        "reasoning": {"S": "test"},
        "gpt_agreed": {"BOOL": True},
        "source_batch_id": {"S": "batch-1"},
        "example_ids": {"SS": ["id"]},
        "image_id": {"S": "image"},
        "receipt_id": {"N": "101"},
        "timestamp": {"S": datetime.now().isoformat()},
    }
    with pytest.raises(ValueError, match="Error converting item to LabelHygieneResult"):
        item_to_label_hygiene_result(item)


# === REPR, STR, ITER, EQ, HASH ===


@pytest.mark.unit
def test_label_hygiene_result_repr(example_label_hygiene_result):
    text = repr(example_label_hygiene_result)
    assert "LabelHygieneResult(" in text
    assert "alias='SUB_TOTAL'" in text
    assert "image_id='b98df7e6-43f5-43ad-9ee3-e39070c7d9df'" in text


@pytest.mark.unit
def test_label_hygiene_result_str(example_label_hygiene_result):
    text = str(example_label_hygiene_result)
    assert "LabelHygieneResult(" in text
    assert "alias='SUB_TOTAL'" in text
    assert "image_id='b98df7e6-43f5-43ad-9ee3-e39070c7d9df'" in text


@pytest.mark.unit
def test_label_hygiene_result_iter(example_label_hygiene_result):
    d = dict(example_label_hygiene_result)
    assert d["receipt_id"] == 101
    assert d["gpt_agreed"] is True


@pytest.mark.unit
def test_label_hygiene_result_eq_and_hash(example_label_hygiene_result):
    item = example_label_hygiene_result.to_item()
    clone = item_to_label_hygiene_result(item)
    assert clone == example_label_hygiene_result
    assert hash(clone) == hash(example_label_hygiene_result)
    assert example_label_hygiene_result != "not-a-label"
