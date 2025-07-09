from datetime import datetime

import pytest

from receipt_dynamo.constants import LabelStatus
from receipt_dynamo.entities.label_metadata import (
    LabelMetadata,
    item_to_label_metadata,
)


# Fixture
@pytest.fixture
def example_label_metadata():
    return LabelMetadata(
        label="SUBTOTAL",
        status=LabelStatus.ACTIVE.value,
        aliases=["SUB_TOTAL", "Sub Total"],
        description="Subtotal of all items before tax",
        schema_version=1,
        last_updated=datetime(2024, 1, 1, 10, 0, 0),
        label_target="value",
        receipt_refs=[("img-uuid", 101)],
    )


# Validation Tests
@pytest.mark.parametrize(
    "field,value,message",
    [
        ("label", 123, "label must be str, got int"),
        ("status", 456, "LabelStatus must be a str or LabelStatus instance"),
        ("aliases", "not-a-list", "aliases must be list, got str"),
        ("description", 789, "description must be str, got int"),
        ("schema_version", "1", "schema_version must be int, got str"),
        ("last_updated", "now", "last_updated must be datetime, got str"),
        ("label_target", 123, "label_target must be str, got int"),
        (
            "receipt_refs",
            [("img", "not-an-int")],
            "receipt_refs must be a list of .* tuples",
        ),
    ],
)
def test_label_metadata_field_validation(field, value, message):
    kwargs = dict(
        label="SUBTOTAL",
        status=LabelStatus.ACTIVE.value,
        aliases=["SUB_TOTAL"],
        description="desc",
        schema_version=1,
        last_updated=datetime.now(),
        label_target="value",
        receipt_refs=[("img", 1)],
    )
    kwargs[field] = value
    with pytest.raises(ValueError, match=message):
        LabelMetadata(**kwargs)


def test_label_metadata_invalid_status_enum():
    with pytest.raises(ValueError, match="LabelStatus must be one of"):
        LabelMetadata(
            label="FOO",
            status="INVALID",  # not in LabelStatus
            aliases=[],
            description="bad status",
            schema_version=1,
            last_updated=datetime.now(),
            label_target="value",
            receipt_refs=[("img", 1)],
        )


# Serialization Tests
def test_label_metadata_valid(example_label_metadata):
    assert example_label_metadata.label == "SUBTOTAL"


def test_label_metadata_roundtrip(example_label_metadata):
    item = example_label_metadata.to_item()
    restored = item_to_label_metadata(item)
    assert restored.label == example_label_metadata.label
    assert restored.status == example_label_metadata.status
    assert restored.aliases == example_label_metadata.aliases
    assert restored.description == example_label_metadata.description
    assert restored.schema_version == example_label_metadata.schema_version
    assert restored.last_updated == example_label_metadata.last_updated
    assert restored.label_target == example_label_metadata.label_target
    assert restored.receipt_refs == example_label_metadata.receipt_refs


def test_label_metadata_deserialization_invalid_date_format():
    item = {
        "PK": {"S": "LABEL#SUBTOTAL"},
        "SK": {"S": "METADATA"},
        "status": {"S": "ACTIVE"},
        "aliases": {"SS": ["A"]},
        "description": {"S": "example"},
        "schema_version": {"N": "1"},
        "last_updated": {"S": "not-a-date"},
    }
    with pytest.raises(
        ValueError, match="Error converting item to LabelMetadata"
    ):
        item_to_label_metadata(item)


def test_label_metadata_missing_keys_in_itemToLabelMetadata():
    item = {
        "PK": {"S": "LABEL#SUBTOTAL"},
        "SK": {"S": "METADATA"},
        # missing required keys like status, aliases
    }
    with pytest.raises(ValueError, match="missing keys"):
        item_to_label_metadata(item)


# Representation Tests
def test_label_metadata_repr(example_label_metadata):
    s = repr(example_label_metadata)
    assert "LabelMetadata(" in s
    assert "SUBTOTAL" in s
    assert "ACTIVE" in s


def test_label_metadata_str(example_label_metadata):
    assert str(example_label_metadata) == repr(example_label_metadata)


def test_label_metadata_keys(example_label_metadata):
    keys = example_label_metadata.key
    assert keys["PK"]["S"] == "LABEL#SUBTOTAL"
    assert keys["SK"]["S"] == "METADATA"

    gsi = example_label_metadata.gsi1_key()
    assert gsi["GSI1PK"]["S"] == "LABEL#SUBTOTAL"
    assert gsi["GSI1SK"]["S"] == "METADATA"
