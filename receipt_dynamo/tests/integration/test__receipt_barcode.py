"""Integration tests for the ReceiptBarcode data-access layer."""

import pytest

from receipt_dynamo import DynamoClient, ReceiptBarcode

IMAGE_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"


def _barcode(barcode_id: int, receipt_id: int = 1) -> ReceiptBarcode:
    return ReceiptBarcode(
        image_id=IMAGE_ID,
        receipt_id=receipt_id,
        barcode_id=barcode_id,
        symbology="Code128",
        text=f"payload-{barcode_id}",
        bounding_box={"x": 0.1, "y": 0.32, "width": 0.78, "height": 0.06},
        top_right={"x": 0.88, "y": 0.38},
        top_left={"x": 0.1, "y": 0.38},
        bottom_right={"x": 0.88, "y": 0.32},
        bottom_left={"x": 0.1, "y": 0.32},
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=0.93,
    )


@pytest.mark.integration
def test_delete_receipt_barcodes_from_receipt(dynamodb_table):
    """The idempotent delete helper removes exactly one receipt's barcodes.

    Exercises the strongly consistent base-table read used by
    ``delete_receipt_barcodes_from_receipt`` so the delete-then-re-add
    backfill cannot leave orphans behind.
    """
    client = DynamoClient(dynamodb_table)
    client.add_receipt_barcodes([_barcode(0), _barcode(1)])
    client.add_receipt_barcodes([_barcode(0, receipt_id=2)])

    assert len(client.list_receipt_barcodes_from_receipt(IMAGE_ID, 1)) == 2

    client.delete_receipt_barcodes_from_receipt(IMAGE_ID, 1)

    # Receipt 1 is emptied; the sibling receipt's barcode is untouched.
    assert client.list_receipt_barcodes_from_receipt(IMAGE_ID, 1) == []
    assert len(client.list_receipt_barcodes_from_receipt(IMAGE_ID, 2)) == 1


@pytest.mark.integration
def test_delete_then_readd_is_idempotent(dynamodb_table):
    """Delete-then-add re-runs cleanly (no conditional-put collision)."""
    client = DynamoClient(dynamodb_table)
    client.add_receipt_barcodes([_barcode(0), _barcode(1)])

    client.delete_receipt_barcodes_from_receipt(IMAGE_ID, 1)
    client.add_receipt_barcodes([_barcode(0)])

    remaining = client.list_receipt_barcodes_from_receipt(IMAGE_ID, 1)
    assert [b.barcode_id for b in remaining] == [0]


@pytest.mark.integration
def test_delete_receipt_barcodes_from_receipt_empty(dynamodb_table):
    """Deleting when nothing exists is a no-op, not an error."""
    client = DynamoClient(dynamodb_table)
    # Should not raise.
    client.delete_receipt_barcodes_from_receipt(IMAGE_ID, 99)
    assert client.list_receipt_barcodes_from_receipt(IMAGE_ID, 99) == []
