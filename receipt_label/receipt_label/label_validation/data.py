"""Data utilities for label validation."""

from collections import Counter
from dataclasses import dataclass
from typing import Literal, Optional

from receipt_dynamo.constants import ValidationStatus  # type: ignore
from receipt_dynamo.entities import ReceiptWordLabel  # type: ignore

from receipt_label.utils import get_client_manager
from receipt_label.utils.client_manager import ClientManager


@dataclass
class LabelValidationResult:
    """Result produced by a label validator."""

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
    client_manager: Optional[ClientManager] = None,
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


def _separate_valid_and_invalid_labels(
    label_validation_results: list[
        tuple[LabelValidationResult, ReceiptWordLabel]
    ],
) -> tuple[
    list[tuple[LabelValidationResult, ReceiptWordLabel]],
    list[tuple[LabelValidationResult, ReceiptWordLabel]],
]:
    """Separate labels based on validation results."""
    valid_labels = []
    invalid_labels = []
    for label_validation_result, label in label_validation_results:
        if label_validation_result.is_consistent:
            valid_labels.append((label_validation_result, label))
        else:
            invalid_labels.append((label_validation_result, label))
    return valid_labels, invalid_labels


def _group_labels_by_pinecone_id(
    labels: list[tuple[LabelValidationResult, ReceiptWordLabel]],
) -> dict[str, list[ReceiptWordLabel]]:
    """Group labels by their Pinecone ID."""
    grouped = {}
    for label_result, label in labels:
        grouped.setdefault(label_result.pinecone_id, []).append(label)
    return grouped


def _update_pinecone_metadata(
    valid_by_id: dict[str, list[ReceiptWordLabel]],
    invalid_by_id: dict[str, list[ReceiptWordLabel]],
    client_manager: ClientManager,
) -> None:
    """Update ChromaDB vector metadata with validation results."""
    all_chroma_ids = set(valid_by_id.keys()) | set(invalid_by_id.keys())
    vectors_by_id = {}
    
    # Get vectors from ChromaDB
    results = client_manager.chroma.get_by_ids(
        "words",
        list(all_chroma_ids),
        include=["metadatas"]
    )
    
    # Process each vector
    if results and 'ids' in results:
        for i, chroma_id in enumerate(results['ids']):
            metadata = results['metadatas'][i] if 'metadatas' in results else {}
            valid_labels = set(metadata.get("valid_labels", []))
            invalid_labels = set(metadata.get("invalid_labels", []))

            # Add valid labels
            for label in valid_by_id.get(chroma_id, []):
                valid_labels.add(label.label)
                # If label is now valid, ensure it's removed from invalid_labels
                if label.label in invalid_labels:
                    invalid_labels.discard(label.label)

            # Add invalid labels and remove them from valid_labels
            for label in invalid_by_id.get(chroma_id, []):
                invalid_labels.add(label.label)
                if label.label in valid_labels:
                    valid_labels.discard(label.label)

            metadata["valid_labels"] = list(valid_labels)
            metadata["invalid_labels"] = list(invalid_labels)
            vectors_by_id[chroma_id] = metadata

    # Update metadata for each vector in ChromaDB
    collection = client_manager.chroma.get_collection("words")
    for chroma_id, metadata in vectors_by_id.items():
        collection.update(
            ids=[chroma_id],
            metadatas=[metadata]
        )


def _update_dynamodb_labels(
    labels_to_mark_as_valid: list[
        tuple[LabelValidationResult, ReceiptWordLabel]
    ],
    labels_to_mark_as_invalid: list[
        tuple[LabelValidationResult, ReceiptWordLabel]
    ],
    client_manager: ClientManager,
) -> None:
    """Update label validation status in DynamoDB."""
    labels_to_update: list[ReceiptWordLabel] = []

    for _, label in labels_to_mark_as_valid:
        label.validation_status = ValidationStatus.VALID.value
        labels_to_update.append(label)

    for _, label in labels_to_mark_as_invalid:
        label.validation_status = ValidationStatus.INVALID.value
        labels_to_update.append(label)

    client_manager.dynamo.updateReceiptWordLabels(labels_to_update)


def update_labels(
    label_validation_results: list[
        tuple[LabelValidationResult, ReceiptWordLabel]
    ],
    client_manager: Optional[ClientManager] = None,
):
    """Apply validation results to DynamoDB and Pinecone."""
    # Applies validation results to both DynamoDB and Pinecone.
    # - Separates valid and invalid labels based on ``is_consistent`` flag.
    # - Updates Pinecone vector metadata:
    #   * Adds the label to ``valid_labels`` if consistent.
    #   * Moves the label to ``invalid_labels`` and removes it from
    #     ``valid_labels`` if inconsistent.
    #   * Ensures that each Pinecone ID is upserted once with merged metadata.
    # - Updates corresponding label validation status in DynamoDB to either
    #   VALIDATED or INVALID.

    if client_manager is None:
        client_manager = get_client_manager()

    # Separate labels based on validation results
    labels_to_mark_as_valid, labels_to_mark_as_invalid = (
        _separate_valid_and_invalid_labels(label_validation_results)
    )

    # Group labels by pinecone_id
    valid_by_id = _group_labels_by_pinecone_id(labels_to_mark_as_valid)
    invalid_by_id = _group_labels_by_pinecone_id(labels_to_mark_as_invalid)

    # Update Pinecone metadata
    _update_pinecone_metadata(valid_by_id, invalid_by_id, client_manager)

    # Update DynamoDB labels
    _update_dynamodb_labels(
        labels_to_mark_as_valid,
        labels_to_mark_as_invalid,
        client_manager,
    )
