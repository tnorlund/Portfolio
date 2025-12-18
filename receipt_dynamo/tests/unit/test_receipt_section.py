# pylint: disable=redefined-outer-name
from copy import deepcopy
from datetime import datetime

import pytest

from receipt_dynamo.constants import SectionType

# Fix circular import by importing directly from the entity module
from receipt_dynamo.entities.receipt_section import (
    ReceiptSection,
    item_to_receipt_section,
)


@pytest.fixture
def example_receipt_section():
    """Fixture to create a sample ReceiptSection for testing."""
    return ReceiptSection(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        section_type=SectionType.HEADER,
        line_ids=[1, 2, 3, 4],
        created_at=datetime(2023, 1, 1, 12, 0, 0),
    )


@pytest.mark.unit
def test_receipt_section_init_valid(example_receipt_section):
    """Test that a ReceiptSection can be created with valid parameters."""
    assert example_receipt_section.receipt_id == 1
    assert example_receipt_section.image_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert example_receipt_section.section_type == "HEADER"
    assert example_receipt_section.line_ids == [1, 2, 3, 4]
    assert example_receipt_section.created_at == datetime(2023, 1, 1, 12, 0, 0)


@pytest.mark.unit
def test_receipt_section_init_with_string_section_type():
    """Test that a ReceiptSection can be created with a string section_type."""
    section = ReceiptSection(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        section_type="HEADER",
        line_ids=[1, 2, 3],
        created_at=datetime(2023, 1, 1, 12, 0, 0),
    )
    assert section.section_type == "HEADER"


@pytest.mark.unit
def test_receipt_section_init_with_string_created_at():
    """Test that a ReceiptSection can be created with an ISO format string."""
    section = ReceiptSection(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        section_type=SectionType.HEADER,
        line_ids=[1, 2, 3, 4],
        created_at="2023-01-01T12:00:00",
    )
    assert section.created_at == datetime(2023, 1, 1, 12, 0, 0)


@pytest.mark.unit
def test_receipt_section_init_invalid_receipt_id():
    """Test that ReceiptSection raises an error for invalid receipt_id."""
    with pytest.raises(ValueError, match="receipt_id must be an integer"):
        ReceiptSection(
            receipt_id="1",
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            section_type=SectionType.HEADER,
            line_ids=[1, 2, 3, 4],
            created_at=datetime(2023, 1, 1, 12, 0, 0),
        )

    with pytest.raises(ValueError, match="receipt_id must be positive"):
        ReceiptSection(
            receipt_id=-1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            section_type=SectionType.HEADER,
            line_ids=[1, 2, 3, 4],
            created_at=datetime(2023, 1, 1, 12, 0, 0),
        )


@pytest.mark.unit
def test_receipt_section_init_invalid_uuid():
    """Test that ReceiptSection raises an error for invalid image_id values."""
    with pytest.raises(ValueError, match="uuid must be a string"):
        ReceiptSection(
            receipt_id=1,
            image_id=123,
            section_type=SectionType.HEADER,
            line_ids=[1, 2, 3, 4],
            created_at=datetime(2023, 1, 1, 12, 0, 0),
        )

    with pytest.raises(ValueError, match="uuid must be a valid UUID"):
        ReceiptSection(
            receipt_id=1,
            image_id="not-a-valid-uuid",
            section_type=SectionType.HEADER,
            line_ids=[1, 2, 3, 4],
            created_at=datetime(2023, 1, 1, 12, 0, 0),
        )


@pytest.mark.unit
def test_receipt_section_init_invalid_section_type():
    """Test that ReceiptSection raises an error for invalid section_type."""
    with pytest.raises(ValueError):
        ReceiptSection(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            section_type="INVALID_TYPE",
            line_ids=[1, 2, 3, 4],
            created_at=datetime(2023, 1, 1, 12, 0, 0),
        )

    with pytest.raises(ValueError):
        ReceiptSection(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            section_type=123,
            line_ids=[1, 2, 3, 4],
            created_at=datetime(2023, 1, 1, 12, 0, 0),
        )


@pytest.mark.unit
def test_receipt_section_init_invalid_line_ids():
    """Test that ReceiptSection raises an error for invalid line_ids values."""
    with pytest.raises(ValueError, match="line_ids must be a list"):
        ReceiptSection(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            section_type=SectionType.HEADER,
            line_ids="not a list",
            created_at=datetime(2023, 1, 1, 12, 0, 0),
        )

    with pytest.raises(ValueError, match="line_ids must not be empty"):
        ReceiptSection(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            section_type=SectionType.HEADER,
            line_ids=[],
            created_at=datetime(2023, 1, 1, 12, 0, 0),
        )

    with pytest.raises(ValueError, match="line_ids must contain only integers"):
        ReceiptSection(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            section_type=SectionType.HEADER,
            line_ids=[1, "2", 3],
            created_at=datetime(2023, 1, 1, 12, 0, 0),
        )


@pytest.mark.unit
def test_receipt_section_init_invalid_created_at():
    """Test that ReceiptSection raises an error for invalid created_at."""
    with pytest.raises(ValueError):
        ReceiptSection(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            section_type=SectionType.HEADER,
            line_ids=[1, 2, 3, 4],
            created_at="not a valid date",
        )

    with pytest.raises(ValueError):
        ReceiptSection(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            section_type=SectionType.HEADER,
            line_ids=[1, 2, 3, 4],
            created_at=123,
        )


@pytest.mark.unit
def test_receipt_section_to_item(example_receipt_section):
    """Test that to_item() returns the correctly formatted DynamoDB item."""
    item = example_receipt_section.to_item()
    assert item["PK"]["S"] == "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert item["SK"]["S"] == "RECEIPT#00001#SECTION#HEADER"
    assert item["TYPE"]["S"] == "RECEIPT_SECTION"
    assert item["section_type"]["S"] == "HEADER"
    assert item["line_ids"]["L"] == [
        {"N": "1"},
        {"N": "2"},
        {"N": "3"},
        {"N": "4"},
    ]
    assert item["created_at"]["S"] == "2023-01-01T12:00:00"


@pytest.mark.unit
def test_receipt_section_eq(example_receipt_section):
    """Test that two ReceiptSection objects with same attributes are equal."""
    section1 = example_receipt_section
    section2 = deepcopy(example_receipt_section)
    assert section1 == section2

    # Test inequality for different attributes
    section2.line_ids = [1, 2, 3]
    assert section1 != section2

    section2.line_ids = section1.line_ids
    section2.section_type = "FOOTER"
    assert section1 != section2

    # Test inequality with non-ReceiptSection objects
    assert section1 != "not a section"


@pytest.mark.unit
def test_receipt_section_repr(example_receipt_section):
    """Test that __repr__() returns the expected string representation."""
    repr_str = repr(example_receipt_section)
    assert "receipt_id=1" in repr_str
    assert "image_id='3f52804b-2fad-4e00-92c8-b593da3a8ed3'" in repr_str
    assert "section_type='HEADER'" in repr_str
    assert "line_ids=[1, 2, 3, 4]" in repr_str
    assert "created_at='2023-01-01T12:00:00'" in repr_str


@pytest.mark.unit
def test_receipt_section_iter(example_receipt_section):
    """Test that iterating over a ReceiptSection returns all attributes."""
    section_dict = dict(example_receipt_section)
    assert section_dict["receipt_id"] == 1
    assert section_dict["image_id"] == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert section_dict["section_type"] == "HEADER"
    assert section_dict["line_ids"] == [1, 2, 3, 4]
    assert section_dict["created_at"] == "2023-01-01T12:00:00"


@pytest.mark.unit
def test_item_to_receipt_section(example_receipt_section):
    """Test that item_to_receipt_section correctly converts a DynamoDB item."""
    item = example_receipt_section.to_item()
    section = item_to_receipt_section(item)
    assert section == example_receipt_section

    # Test with missing keys
    with pytest.raises(ValueError, match="Item is missing required keys"):
        item_to_receipt_section({})

    # Test with invalid item
    invalid_item = {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "RECEIPT#00001#SECTION#INVALID_TYPE"},
        "TYPE": {"S": "RECEIPT_SECTION"},
        "section_type": {"S": "INVALID_TYPE"},
        "line_ids": {"L": [{"N": "1"}, {"N": "2"}]},
        "created_at": {"S": "2023-01-01T12:00:00"},
    }
    with pytest.raises(ValueError, match="Error converting item to ReceiptSection"):
        item_to_receipt_section(invalid_item)
