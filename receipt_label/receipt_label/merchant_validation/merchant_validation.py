from typing import Tuple, List


from receipt_dynamo.entities import (
    ReceiptWordLabel,
    ReceiptWord,
    Receipt,
    ReceiptLine,
    ReceiptWord,
    ReceiptLetter,
    ReceiptWordTag,
)
from receipt_label.utils import get_clients

dynamo_client, openai_client, _ = get_clients()


def list_receipts_for_merchant_validation() -> List[Tuple[str, int]]:
    """
    Lists all receipts that do not have receipt metadata.

    Returns:
        List[Tuple[str, int]]: A list of tuples containing the image_id and
            receipt_id of the receipts that do not have receipt metadata.
    """
    receipts, lek = dynamo_client.listReceipts(limit=25)
    while lek:
        next_receipts, lek = dynamo_client.listReceipts(
            limit=25, lastEvaluatedKey=lek
        )
        receipts.extend(next_receipts)
    # Filter out receipts that have receipt metadata
    receipt_metadatas = dynamo_client.getReceiptMetadatas(
        [
            {
                "PK": {"S": f"IMAGE#{receipt.image_id}"},
                "SK": {"S": f"RECEIPT#{receipt.receipt_id:05d}#METADATA"},
            }
            for receipt in receipts
        ]
    )
    # Create a set of tuples with (image_id, receipt_id) from metadata for efficient lookup
    metadata_keys = {
        (metadata.image_id, metadata.receipt_id)
        for metadata in receipt_metadatas
    }

    # Return receipts that don't have corresponding metadata
    return [
        (receipt.image_id, receipt.receipt_id)
        for receipt in receipts
        if (receipt.image_id, receipt.receipt_id) not in metadata_keys
    ]


def get_receipt_details(image_id: str, receipt_id: int) -> Tuple[
    Receipt,
    list[ReceiptLine],
    list[ReceiptWord],
    list[ReceiptLetter],
    list[ReceiptWordTag],
    list[ReceiptWordLabel],
]:
    """Get a receipt with its details"""
    (
        receipt,
        lines,
        words,
        letters,
        tags,
        labels,
    ) = dynamo_client.getReceiptDetails(image_id, receipt_id)
    return (
        receipt,
        lines,
        words,
        letters,
        tags,
        labels,
    )
