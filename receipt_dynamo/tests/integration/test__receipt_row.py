from datetime import datetime
from typing import Literal

import pytest

from receipt_dynamo import DynamoClient
from receipt_dynamo.data.shared_exceptions import EntityAlreadyExistsError
from receipt_dynamo.entities.receipt_row import ReceiptRow

pytestmark = [pytest.mark.integration]


def _make_row(row_id: int, line_ids: list[int], y_min: float = 0.1):
    now = datetime.utcnow().replace(microsecond=0)
    return ReceiptRow(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        row_id=row_id,
        line_ids=line_ids,
        grouping_version="visual-rows-v1",
        y_min=y_min,
        y_max=y_min + 0.02,
        x_min=0.05,
        x_max=0.95,
        created_at=now,
    )


@pytest.fixture
def sample_receipt_row():
    """Returns a valid ReceiptRow object with placeholder data."""
    return _make_row(5, [5, 6])


def test_add_receipt_row(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_row: ReceiptRow,
):
    client = DynamoClient(dynamodb_table)

    client.add_receipt_row(sample_receipt_row)

    retrieved = client.get_receipt_row(
        sample_receipt_row.receipt_id,
        sample_receipt_row.image_id,
        sample_receipt_row.row_id,
    )
    assert retrieved == sample_receipt_row


def test_add_receipt_row_duplicate_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_row: ReceiptRow,
):
    client = DynamoClient(dynamodb_table)
    client.add_receipt_row(sample_receipt_row)
    with pytest.raises(EntityAlreadyExistsError, match="already exists"):
        client.add_receipt_row(sample_receipt_row)


def test_add_receipt_rows_batch(dynamodb_table: Literal["MyMockedTable"]):
    client = DynamoClient(dynamodb_table)
    rows = [
        _make_row(1, [1, 2], y_min=0.1),
        _make_row(3, [3], y_min=0.2),
        _make_row(4, [4, 5, 6], y_min=0.3),
    ]

    client.add_receipt_rows(rows)

    retrieved = client.get_receipt_rows_from_receipt(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1
    )
    assert retrieved == rows  # SK order == row_id order


def test_update_receipt_row(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_row: ReceiptRow,
):
    client = DynamoClient(dynamodb_table)
    client.add_receipt_row(sample_receipt_row)

    sample_receipt_row.line_ids = [5, 6, 7]
    sample_receipt_row.x_max = 0.99
    client.update_receipt_row(sample_receipt_row)

    retrieved = client.get_receipt_row(
        sample_receipt_row.receipt_id,
        sample_receipt_row.image_id,
        sample_receipt_row.row_id,
    )
    assert retrieved.line_ids == [5, 6, 7]
    assert retrieved.x_max == 0.99


def test_update_receipt_rows_batch(
    dynamodb_table: Literal["MyMockedTable"],
):
    client = DynamoClient(dynamodb_table)
    rows = [_make_row(1, [1]), _make_row(2, [2, 3], y_min=0.2)]
    client.add_receipt_rows(rows)

    rows[0].grouping_version = "visual-rows-v2"
    rows[1].grouping_version = "visual-rows-v2"
    client.update_receipt_rows(rows)

    retrieved = client.get_receipt_rows_from_receipt(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1
    )
    assert all(r.grouping_version == "visual-rows-v2" for r in retrieved)


def test_delete_receipt_row(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_row: ReceiptRow,
):
    client = DynamoClient(dynamodb_table)
    client.add_receipt_row(sample_receipt_row)

    client.delete_receipt_row(
        sample_receipt_row.receipt_id,
        sample_receipt_row.image_id,
        sample_receipt_row.row_id,
    )

    with pytest.raises(ValueError, match="(does not exist|not found)"):
        client.get_receipt_row(
            sample_receipt_row.receipt_id,
            sample_receipt_row.image_id,
            sample_receipt_row.row_id,
        )


def test_delete_receipt_rows_batch(
    dynamodb_table: Literal["MyMockedTable"],
):
    client = DynamoClient(dynamodb_table)
    rows = [_make_row(1, [1]), _make_row(2, [2], y_min=0.2)]
    client.add_receipt_rows(rows)

    client.delete_receipt_rows(rows)

    assert (
        client.get_receipt_rows_from_receipt(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1
        )
        == []
    )


def test_get_receipt_rows_from_receipt_scoped_to_receipt(
    dynamodb_table: Literal["MyMockedTable"],
):
    client = DynamoClient(dynamodb_table)
    row_r1 = _make_row(1, [1])
    row_r2 = ReceiptRow(
        receipt_id=2,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        row_id=1,
        line_ids=[1],
        grouping_version="visual-rows-v1",
        y_min=0.1,
        y_max=0.12,
        x_min=0.05,
        x_max=0.95,
        created_at=datetime.utcnow().replace(microsecond=0),
    )
    client.add_receipt_rows([row_r1, row_r2])

    assert client.get_receipt_rows_from_receipt(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1
    ) == [row_r1]
    assert client.get_receipt_rows_from_receipt(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3", 2
    ) == [row_r2]


def test_receipt_row_list(dynamodb_table: Literal["MyMockedTable"]):
    client = DynamoClient(dynamodb_table)
    rows = [
        _make_row(1, [1, 2], y_min=0.1),
        _make_row(3, [3], y_min=0.2),
    ]
    for row in rows:
        client.add_receipt_row(row)

    returned_rows, _ = client.list_receipt_rows()

    for row in rows:
        assert row in returned_rows
