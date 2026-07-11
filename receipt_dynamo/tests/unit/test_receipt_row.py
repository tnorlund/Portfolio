# pylint: disable=redefined-outer-name
from copy import deepcopy
from datetime import datetime

import pytest

# Fix circular import by importing directly from the entity module
from receipt_dynamo.entities.receipt_row import (
    ReceiptRow,
    item_to_receipt_row,
)


@pytest.fixture
def example_receipt_row():
    """Fixture to create a sample ReceiptRow for testing."""
    return ReceiptRow(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        row_id=5,
        line_ids=[5, 6, 7],
        grouping_version="visual-rows-v1",
        y_min=0.10,
        y_max=0.15,
        x_min=0.05,
        x_max=0.95,
        created_at=datetime(2026, 7, 10, 12, 0, 0),
    )


@pytest.mark.unit
def test_receipt_row_init_valid(example_receipt_row):
    """Test that a ReceiptRow can be created with valid parameters."""
    assert example_receipt_row.receipt_id == 1
    assert (
        example_receipt_row.image_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    )
    assert example_receipt_row.row_id == 5
    assert example_receipt_row.line_ids == [5, 6, 7]
    assert example_receipt_row.grouping_version == "visual-rows-v1"
    assert example_receipt_row.y_min == 0.10
    assert example_receipt_row.y_max == 0.15
    assert example_receipt_row.x_min == 0.05
    assert example_receipt_row.x_max == 0.95
    assert example_receipt_row.created_at == datetime(2026, 7, 10, 12, 0, 0)


@pytest.mark.unit
def test_receipt_row_init_with_string_created_at():
    """Test that a ReceiptRow can be created with an ISO format string."""
    row = ReceiptRow(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        row_id=3,
        line_ids=[3],
        grouping_version="visual-rows-v1",
        y_min=0.2,
        y_max=0.25,
        x_min=0.1,
        x_max=0.5,
        created_at="2026-07-10T12:00:00",
    )
    assert row.created_at == datetime(2026, 7, 10, 12, 0, 0)


@pytest.mark.unit
def test_receipt_row_single_line_row():
    """A one-line visual row is valid; row_id equals its only line."""
    row = ReceiptRow(
        receipt_id=2,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        row_id=0,
        line_ids=[0],
        grouping_version="visual-rows-v1",
        y_min=0.0,
        y_max=0.02,
        x_min=0.0,
        x_max=1.0,
        created_at=datetime(2026, 7, 10),
    )
    assert row.row_id == 0
    assert row.line_ids == [0]


@pytest.mark.unit
def test_receipt_row_init_invalid_receipt_id():
    """Test that ReceiptRow raises an error for invalid receipt_id."""
    with pytest.raises(ValueError, match="receipt_id must be an integer"):
        ReceiptRow(
            receipt_id="1",
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            row_id=5,
            line_ids=[5],
            grouping_version="visual-rows-v1",
            y_min=0.1,
            y_max=0.2,
            x_min=0.1,
            x_max=0.9,
            created_at=datetime(2026, 7, 10),
        )

    with pytest.raises(ValueError, match="receipt_id must be positive"):
        ReceiptRow(
            receipt_id=-1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            row_id=5,
            line_ids=[5],
            grouping_version="visual-rows-v1",
            y_min=0.1,
            y_max=0.2,
            x_min=0.1,
            x_max=0.9,
            created_at=datetime(2026, 7, 10),
        )


@pytest.mark.unit
def test_receipt_row_init_invalid_uuid():
    """Test that ReceiptRow raises an error for invalid image_id values."""
    with pytest.raises(ValueError, match="uuid must be a string"):
        ReceiptRow(
            receipt_id=1,
            image_id=123,
            row_id=5,
            line_ids=[5],
            grouping_version="visual-rows-v1",
            y_min=0.1,
            y_max=0.2,
            x_min=0.1,
            x_max=0.9,
            created_at=datetime(2026, 7, 10),
        )

    with pytest.raises(ValueError, match="uuid must be a valid UUID"):
        ReceiptRow(
            receipt_id=1,
            image_id="not-a-valid-uuid",
            row_id=5,
            line_ids=[5],
            grouping_version="visual-rows-v1",
            y_min=0.1,
            y_max=0.2,
            x_min=0.1,
            x_max=0.9,
            created_at=datetime(2026, 7, 10),
        )


@pytest.mark.unit
def test_receipt_row_init_invalid_row_id():
    """Test that ReceiptRow raises an error for invalid row_id values."""
    with pytest.raises(ValueError, match="row_id must be an integer"):
        ReceiptRow(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            row_id="5",
            line_ids=[5],
            grouping_version="visual-rows-v1",
            y_min=0.1,
            y_max=0.2,
            x_min=0.1,
            x_max=0.9,
            created_at=datetime(2026, 7, 10),
        )

    with pytest.raises(ValueError, match="row_id must be non-negative"):
        ReceiptRow(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            row_id=-5,
            line_ids=[-5],
            grouping_version="visual-rows-v1",
            y_min=0.1,
            y_max=0.2,
            x_min=0.1,
            x_max=0.9,
            created_at=datetime(2026, 7, 10),
        )


@pytest.mark.unit
def test_receipt_row_init_invalid_line_ids():
    """Test that ReceiptRow raises an error for invalid line_ids values."""
    with pytest.raises(ValueError, match="line_ids must be a list"):
        ReceiptRow(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            row_id=5,
            line_ids="not a list",
            grouping_version="visual-rows-v1",
            y_min=0.1,
            y_max=0.2,
            x_min=0.1,
            x_max=0.9,
            created_at=datetime(2026, 7, 10),
        )

    with pytest.raises(ValueError, match="line_ids must not be empty"):
        ReceiptRow(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            row_id=5,
            line_ids=[],
            grouping_version="visual-rows-v1",
            y_min=0.1,
            y_max=0.2,
            x_min=0.1,
            x_max=0.9,
            created_at=datetime(2026, 7, 10),
        )

    with pytest.raises(
        ValueError, match="line_ids must contain only integers"
    ):
        ReceiptRow(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            row_id=5,
            line_ids=[5, "6"],
            grouping_version="visual-rows-v1",
            y_min=0.1,
            y_max=0.2,
            x_min=0.1,
            x_max=0.9,
            created_at=datetime(2026, 7, 10),
        )

    with pytest.raises(
        ValueError, match="line_ids must not contain duplicates"
    ):
        ReceiptRow(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            row_id=5,
            line_ids=[5, 6, 5],
            grouping_version="visual-rows-v1",
            y_min=0.1,
            y_max=0.2,
            x_min=0.1,
            x_max=0.9,
            created_at=datetime(2026, 7, 10),
        )


@pytest.mark.unit
def test_receipt_row_primary_line_convention_enforced():
    """row_id must equal line_ids[0] (the leftmost/primary line)."""
    with pytest.raises(ValueError, match=r"row_id must equal line_ids\[0\]"):
        ReceiptRow(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            row_id=6,
            line_ids=[5, 6],
            grouping_version="visual-rows-v1",
            y_min=0.1,
            y_max=0.2,
            x_min=0.1,
            x_max=0.9,
            created_at=datetime(2026, 7, 10),
        )


@pytest.mark.unit
def test_receipt_row_init_invalid_grouping_version():
    """Test that ReceiptRow rejects a missing/invalid grouping_version."""
    for bad in ("", None, 1):
        with pytest.raises(
            ValueError, match="grouping_version must be a non-empty string"
        ):
            ReceiptRow(
                receipt_id=1,
                image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
                row_id=5,
                line_ids=[5],
                grouping_version=bad,
                y_min=0.1,
                y_max=0.2,
                x_min=0.1,
                x_max=0.9,
                created_at=datetime(2026, 7, 10),
            )


@pytest.mark.unit
def test_receipt_row_init_invalid_geometry():
    """Test that ReceiptRow validates its geometry summary."""
    with pytest.raises(ValueError, match="y_min must be a number"):
        ReceiptRow(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            row_id=5,
            line_ids=[5],
            grouping_version="visual-rows-v1",
            y_min="0.1",
            y_max=0.2,
            x_min=0.1,
            x_max=0.9,
            created_at=datetime(2026, 7, 10),
        )

    with pytest.raises(ValueError, match="y_min must be <= y_max"):
        ReceiptRow(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            row_id=5,
            line_ids=[5],
            grouping_version="visual-rows-v1",
            y_min=0.3,
            y_max=0.2,
            x_min=0.1,
            x_max=0.9,
            created_at=datetime(2026, 7, 10),
        )

    with pytest.raises(ValueError, match="x_min must be <= x_max"):
        ReceiptRow(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            row_id=5,
            line_ids=[5],
            grouping_version="visual-rows-v1",
            y_min=0.1,
            y_max=0.2,
            x_min=0.9,
            x_max=0.1,
            created_at=datetime(2026, 7, 10),
        )


@pytest.mark.unit
def test_receipt_row_init_invalid_created_at():
    """Test that ReceiptRow raises an error for invalid created_at."""
    with pytest.raises(ValueError):
        ReceiptRow(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            row_id=5,
            line_ids=[5],
            grouping_version="visual-rows-v1",
            y_min=0.1,
            y_max=0.2,
            x_min=0.1,
            x_max=0.9,
            created_at="not a valid date",
        )

    with pytest.raises(ValueError):
        ReceiptRow(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            row_id=5,
            line_ids=[5],
            grouping_version="visual-rows-v1",
            y_min=0.1,
            y_max=0.2,
            x_min=0.1,
            x_max=0.9,
            created_at=123,
        )


@pytest.mark.unit
def test_receipt_row_to_item(example_receipt_row):
    """Test that to_item() returns the correctly formatted DynamoDB item."""
    item = example_receipt_row.to_item()
    assert item["PK"]["S"] == "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert item["SK"]["S"] == "RECEIPT#00001#ROW#00005"
    assert item["TYPE"]["S"] == "RECEIPT_ROW"
    assert item["line_ids"]["L"] == [{"N": "5"}, {"N": "6"}, {"N": "7"}]
    assert item["grouping_version"]["S"] == "visual-rows-v1"
    assert item["y_min"]["N"] == "0.1"
    assert item["y_max"]["N"] == "0.15"
    assert item["x_min"]["N"] == "0.05"
    assert item["x_max"]["N"] == "0.95"
    assert item["created_at"]["S"] == "2026-07-10T12:00:00"


@pytest.mark.unit
def test_receipt_row_key_matches_chroma_line_id_convention(
    example_receipt_row,
):
    """SK embeds the primary line id — the Chroma lines-collection join."""
    # Chroma lines collection id: IMAGE#{image}#RECEIPT#{r:05d}#LINE#{p:05d}
    item = example_receipt_row.to_item()
    assert item["SK"]["S"].endswith(f"#ROW#{example_receipt_row.row_id:05d}")
    assert example_receipt_row.row_id == example_receipt_row.line_ids[0]


@pytest.mark.unit
def test_receipt_row_eq(example_receipt_row):
    """Test that two ReceiptRow objects with same attributes are equal."""
    row1 = example_receipt_row
    row2 = deepcopy(example_receipt_row)
    assert row1 == row2

    row2.line_ids = [5, 6]
    assert row1 != row2

    row2.line_ids = row1.line_ids
    row2.grouping_version = "visual-rows-v2"
    assert row1 != row2

    assert row1 != "not a row"


@pytest.mark.unit
def test_receipt_row_repr(example_receipt_row):
    """Test that __repr__() returns the expected string representation."""
    repr_str = repr(example_receipt_row)
    assert "receipt_id=1" in repr_str
    assert "image_id='3f52804b-2fad-4e00-92c8-b593da3a8ed3'" in repr_str
    assert "row_id=5" in repr_str
    assert "line_ids=[5, 6, 7]" in repr_str
    assert "grouping_version='visual-rows-v1'" in repr_str
    assert "created_at='2026-07-10T12:00:00'" in repr_str


@pytest.mark.unit
def test_receipt_row_iter(example_receipt_row):
    """Test that iterating over a ReceiptRow returns all attributes."""
    row_dict = dict(example_receipt_row)
    assert row_dict["receipt_id"] == 1
    assert row_dict["image_id"] == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert row_dict["row_id"] == 5
    assert row_dict["line_ids"] == [5, 6, 7]
    assert row_dict["grouping_version"] == "visual-rows-v1"
    assert row_dict["y_min"] == 0.10
    assert row_dict["y_max"] == 0.15
    assert row_dict["x_min"] == 0.05
    assert row_dict["x_max"] == 0.95
    assert row_dict["created_at"] == "2026-07-10T12:00:00"


@pytest.mark.unit
def test_receipt_row_hash(example_receipt_row):
    """Equal rows hash identically."""
    assert hash(example_receipt_row) == hash(deepcopy(example_receipt_row))


@pytest.mark.unit
def test_item_to_receipt_row(example_receipt_row):
    """Test that item_to_receipt_row correctly converts a DynamoDB item."""
    item = example_receipt_row.to_item()
    row = item_to_receipt_row(item)
    assert row == example_receipt_row

    # Test with missing keys
    with pytest.raises(ValueError, match="Item is missing required keys"):
        item_to_receipt_row({})

    # Test with invalid item (row_id not the primary line id)
    invalid_item = dict(example_receipt_row.to_item())
    invalid_item["SK"] = {"S": "RECEIPT#00001#ROW#00099"}
    with pytest.raises(
        ValueError, match="Error converting item to ReceiptRow"
    ):
        item_to_receipt_row(invalid_item)


@pytest.mark.unit
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_receipt_row_non_finite_geometry_rejected(bad):
    """NaN/inf would serialize to Dynamo numbers it rejects — fail early."""
    with pytest.raises(ValueError, match="must be finite"):
        ReceiptRow(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            row_id=5,
            line_ids=[5],
            grouping_version="visual-rows-v1",
            y_min=bad,
            y_max=0.2,
            x_min=0.1,
            x_max=0.9,
            created_at=datetime(2026, 7, 10),
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "pk,sk",
    [
        ("RECEIPT#00001", "RECEIPT#00001#ROW#00005"),  # PK not IMAGE#
        (
            "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "RECEIPT#00001#LINE#00005",  # wrong entity SK
        ),
        (
            "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "RECEIPT#00001#ROW#00005#EXTRA",  # malformed SK
        ),
    ],
)
def test_item_to_receipt_row_rejects_foreign_keys(example_receipt_row, pk, sk):
    """Items with another entity's key shape must not decode as a row."""
    item = dict(example_receipt_row.to_item())
    item["PK"] = {"S": pk}
    item["SK"] = {"S": sk}
    with pytest.raises(
        ValueError, match="Error converting item to ReceiptRow"
    ):
        item_to_receipt_row(item)
