"""Data utilities for label validation."""

# pylint: disable=duplicate-code,line-too-long

from collections import Counter
from dataclasses import dataclass
from typing import Literal

from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.entities import ReceiptWordLabel

from receipt_label.utils import get_client_manager
from receipt_label.utils.client_manager import ClientManager


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


def get_unique_merchants_and_data(
    client_manager: ClientManager = None,
) -> list[dict]:
    """
    Returns a list of dictionaries, each containing:
    - merchant_name: canonical merchant name
    - receipt_count: number of receipts for that merchant
    - image_id: ID of the image
    - receipt_id: ID of the receipt
    Each receipt will have its own dictionary entry, but receipt_count will
    remain the same for all entries of the same merchant.
    """
    if client_manager is None:
        client_manager = get_client_manager()
    receipt_metadatas, _ = client_manager.dynamo.listReceiptMetadatas()
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


def update_labels(
    label_validation_results: list[
        tuple[LabelValidationResult, ReceiptWordLabel]
    ],
    client_manager: ClientManager = None,
):
    """
    Applies validation results to both DynamoDB and Pinecone.

    - Separates valid and invalid labels based on `is_consistent` flag.
    - Updates Pinecone vector metadata:
        * Adds the label to `valid_labels` if consistent.
        * Moves the label to `invalid_labels` and removes it from
          `valid_labels` if inconsistent.
        * Ensures that each Pinecone ID is upserted once with merged
          metadata.
    - Updates corresponding label validation status in DynamoDB to either
      VALIDATED or INVALID.
    """
    labels_to_mark_as_valid: list[
        tuple[LabelValidationResult, ReceiptWordLabel]
    ] = []
    labels_to_mark_as_invalid: list[
        tuple[LabelValidationResult, ReceiptWordLabel]
    ] = []
    for label_validation_result, label in label_validation_results:
        if label_validation_result.is_consistent:
            labels_to_mark_as_valid.append((label_validation_result, label))
        else:
            labels_to_mark_as_invalid.append((label_validation_result, label))

    # Group labels by pinecone_id for valid and invalid
    valid_by_id = {}
    for label_result, label in labels_to_mark_as_valid:
        valid_by_id.setdefault(label_result.pinecone_id, []).append(label)
    invalid_by_id = {}
    for label_result, label in labels_to_mark_as_invalid:
        invalid_by_id.setdefault(label_result.pinecone_id, []).append(label)

    # Combine all pinecone ids to fetch once
    all_pinecone_ids = set(valid_by_id.keys()) | set(invalid_by_id.keys())

    if client_manager is None:
        client_manager = get_client_manager()

    vectors_by_id = {}
    for vector in client_manager.pinecone.fetch(
        list(all_pinecone_ids),
        namespace="words",
    ).vectors.values():
        pinecone_id = vector.id
        valid_labels = set(vector.metadata.get("valid_labels", []))
        invalid_labels = set(vector.metadata.get("invalid_labels", []))

        # Add valid labels
        for label in valid_by_id.get(pinecone_id, []):
            valid_labels.add(label.label)
            # If label is now valid, ensure it's removed from invalid_labels
            if label.label in invalid_labels:
                invalid_labels.discard(label.label)

        # Add invalid labels and remove them from valid_labels
        for label in invalid_by_id.get(pinecone_id, []):
            invalid_labels.add(label.label)
            if label.label in valid_labels:
                valid_labels.discard(label.label)

        vector.metadata["valid_labels"] = list(valid_labels)
        vector.metadata["invalid_labels"] = list(invalid_labels)
        vectors_by_id[pinecone_id] = vector

    # Instead of upsert, use update to only update metadata for each vector
    for v in vectors_by_id.values():
        client_manager.pinecone.update(
            id=v.id,
            set_metadata=v.metadata,
            namespace="words",
        )

    labels_to_update: list[ReceiptWordLabel] = []
    # Update the labels in DynamoDB
    for label_validation_result, label in labels_to_mark_as_valid:
        label.validation_status = ValidationStatus.VALID.value
        labels_to_update.append(label)
    for label_validation_result, label in labels_to_mark_as_invalid:
        label.validation_status = ValidationStatus.INVALID.value
        labels_to_update.append(label)
    client_manager.dynamo.updateReceiptWordLabels(labels_to_update)
