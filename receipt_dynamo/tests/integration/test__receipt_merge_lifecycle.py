"""Consistent receipt lifecycle access and count-only summary finalization."""

from typing import Any, Literal
from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError

from receipt_dynamo import DynamoClient, Receipt
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    EntityValidationError,
    OperationError,
)
from receipt_dynamo.entities.receipt_summary import ReceiptSummary
from receipt_dynamo.entities.receipt_summary_record import ReceiptSummaryRecord

pytestmark = pytest.mark.integration
IMAGE_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
ERROR_SCENARIOS = [
    ("ProvisionedThroughputExceededException", DynamoDBThroughputError),
    ("InternalServerError", DynamoDBServerError),
    ("ValidationException", EntityValidationError),
    ("AccessDeniedException", DynamoDBError),
    ("ResourceNotFoundException", OperationError),
]
OPERATIONS = [
    ("receipt_exists_consistent", "get_item", (IMAGE_ID, 1)),
    ("purge_receipt_children", "query", (IMAGE_ID, 1)),
    ("get_receipts_from_image_consistent", "query", (IMAGE_ID,)),
    ("list_receipt_barcodes_from_receipt_consistent", "query", (IMAGE_ID, 1)),
    ("update_receipt_summary_item_count", "update_item", (IMAGE_ID, 1, 2)),
]


@pytest.mark.parametrize("method,api,args", OPERATIONS)
@pytest.mark.parametrize("code,error_type", ERROR_SCENARIOS)
def test_lifecycle_error_mapping(
    dynamodb_table: Literal["MyMockedTable"],
    method: str,
    api: str,
    args: tuple[Any, ...],
    code: str,
    error_type: type[Exception],
) -> None:
    client = DynamoClient(dynamodb_table)
    with patch.object(
        client._client,
        api,
        side_effect=ClientError({"Error": {"Code": code}}, api),
    ), pytest.raises(error_type):
        getattr(client, method)(*args)


@pytest.mark.parametrize("method,api,args", OPERATIONS)
def test_lifecycle_validates_image_id_before_access(
    dynamodb_table: Literal["MyMockedTable"],
    method: str,
    api: str,
    args: tuple[Any, ...],
) -> None:
    client = DynamoClient(dynamodb_table)
    with patch.object(client._client, api) as access:
        with pytest.raises(EntityValidationError):
            getattr(client, method)(None, *args[1:])
        access.assert_not_called()


@pytest.mark.parametrize("count", [-1, True, 1.5, None])
def test_count_validation(
    dynamodb_table: Literal["MyMockedTable"], count: Any
) -> None:
    client = DynamoClient(dynamodb_table)
    with pytest.raises(EntityValidationError, match="non-negative int"):
        client.update_receipt_summary_item_count(IMAGE_ID, 1, count)


def test_count_finalization_preserves_all_other_summary_fields(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    client = DynamoClient(dynamodb_table)
    assert not client.update_receipt_summary_item_count(IMAGE_ID, 1, 2)
    summary = ReceiptSummaryRecord(
        summary=ReceiptSummary(
            image_id=IMAGE_ID,
            receipt_id=1,
            item_count=0,
            merchant_name="Offline fixture",
            tender_class="card",
            ledger="chase",
            bank_amount=12.34,
            bank_match_confidence=0.99,
        )
    )
    client.add_receipt_summary(summary)
    before = client._client.get_item(
        TableName=client.table_name, Key=summary.key, ConsistentRead=True
    )["Item"]
    assert client.update_receipt_summary_item_count(IMAGE_ID, 1, 2)
    after = client._client.get_item(
        TableName=client.table_name, Key=summary.key, ConsistentRead=True
    )["Item"]
    assert after == {**before, "item_count": {"N": "2"}}
    assert not client.update_receipt_summary_item_count(IMAGE_ID, 1, 2)
    assert client.update_receipt_summary_item_count(IMAGE_ID, 1, 0)


def test_consistent_owner_query_paginates_past_children(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    client = DynamoClient(dynamodb_table)
    for rid in (1, 2):
        client.add_receipt(
            Receipt(
                image_id=IMAGE_ID,
                receipt_id=rid,
                width=100,
                height=200,
                timestamp_added="2026-09-08T00:00:00+00:00",
                raw_s3_bucket="offline-fixture",
                raw_s3_key=f"{rid}.png",
                top_left={"x": 0, "y": 1},
                top_right={"x": 1, "y": 1},
                bottom_left={"x": 0, "y": 0},
                bottom_right={"x": 1, "y": 0},
            )
        )
    client._client.put_item(
        TableName=client.table_name,
        Item={
            "PK": {"S": f"IMAGE#{IMAGE_ID}"},
            "SK": {"S": "RECEIPT#00001#WORD"},
            "TYPE": {"S": "WORD"},
        },
    )
    query = client._client.query

    def limited_query(**kwargs: Any) -> dict[str, Any]:
        assert kwargs["ConsistentRead"] is True
        return query(**kwargs, Limit=1)

    with patch.object(
        client._client, "query", side_effect=limited_query
    ) as read:
        assert [
            r.receipt_id
            for r in client.get_receipts_from_image_consistent(IMAGE_ID)
        ] == [1, 2]
        assert read.call_count == 3
    assert client.receipt_exists_consistent(IMAGE_ID, 1)
    assert not client.receipt_exists_consistent(IMAGE_ID, 3)
