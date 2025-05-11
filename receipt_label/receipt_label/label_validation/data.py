from dataclasses import dataclass
from typing import Literal
from receipt_label.utils import get_clients
from collections import Counter

dynamo_client, _, _ = get_clients()


# Holds the result of a label validation.
@dataclass
class LabelValidationResult:
    image_id: str
    receipt_id: int
    line_id: int
    word_id: int
    label: str
    status: Literal["VALIDATED", "NO_VECTOR"]
    is_consistent: bool
    avg_similarity: float
    neighbors: list[str]
    pinecone_id: str


def get_unique_merchants_and_data() -> list[dict]:
    """
    Returns a list of dictionaries, each containing:
    - merchant_name: canonical merchant name
    - receipt_count: number of receipts for that merchant
    - image_id: ID of the image
    - receipt_id: ID of the receipt
    Each receipt will have its own dictionary entry, but receipt_count will remain the same for all entries of the same merchant.
    """
    receipt_metadatas, _ = dynamo_client.listReceiptMetadatas()
    merchant_counts = Counter(
        metadata.canonical_merchant_name for metadata in receipt_metadatas
    )

    result = []
    for metadata in receipt_metadatas:
        merchant = metadata.canonical_merchant_name
        result.append(
            {
                "merchant_name": merchant,
                "receipt_count": merchant_counts[merchant],
                "image_id": metadata.image_id,
                "receipt_id": metadata.receipt_id,
            }
        )

    return result
