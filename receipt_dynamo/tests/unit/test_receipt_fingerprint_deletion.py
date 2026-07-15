"""Receipt deletion keeps derived rows except the exact additive fingerprint."""

from datetime import datetime
from unittest.mock import MagicMock

from receipt_dynamo.data._receipt import _Receipt
from receipt_dynamo.entities import Receipt


def _receipt(receipt_id: int) -> Receipt:
    return Receipt(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=receipt_id,
        width=10,
        height=20,
        timestamp_added=datetime.now().isoformat(),
        raw_s3_bucket="bucket",
        raw_s3_key=f"receipt-{receipt_id}.png",
        top_left={"x": 0, "y": 0},
        top_right={"x": 10, "y": 0},
        bottom_left={"x": 0, "y": 20},
        bottom_right={"x": 10, "y": 20},
        sha256=f"sha-{receipt_id}",
    )


def _accessor() -> _Receipt:
    accessor = _Receipt()
    accessor.table_name = "receipts"
    accessor._client = MagicMock()
    return accessor


def _fingerprint_key(receipt: Receipt) -> dict[str, dict[str, str]]:
    return {
        "PK": {"S": f"IMAGE#{receipt.image_id}"},
        "SK": {"S": f"RECEIPT#{receipt.receipt_id:05d}#TYPEFACE_FINGERPRINT"},
    }


def test_single_delete_transaction_contains_only_parent_and_fingerprint() -> (
    None
):
    accessor = _accessor()
    receipt = _receipt(1)

    accessor.delete_receipt(receipt)

    items = accessor._client.transact_write_items.call_args.kwargs[
        "TransactItems"
    ]
    assert items == [
        {
            "Delete": {
                "TableName": "receipts",
                "Key": receipt.key,
                "ConditionExpression": "attribute_exists(PK)",
            }
        },
        {
            "Delete": {
                "TableName": "receipts",
                "Key": _fingerprint_key(receipt),
            }
        },
    ]


def test_bulk_delete_keeps_pairs_together_in_twelve_receipt_chunks() -> None:
    accessor = _accessor()
    receipts = [_receipt(receipt_id) for receipt_id in range(1, 14)]

    accessor.delete_receipts(receipts)

    calls = accessor._client.transact_write_items.call_args_list
    assert [len(call.kwargs["TransactItems"]) for call in calls] == [24, 2]
    for call, chunk in zip(calls, (receipts[:12], receipts[12:]), strict=True):
        items = call.kwargs["TransactItems"]
        for index, receipt in enumerate(chunk):
            assert items[index * 2]["Delete"]["Key"] == receipt.key
            assert items[index * 2 + 1]["Delete"]["Key"] == _fingerprint_key(
                receipt
            )
