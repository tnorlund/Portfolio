"""Data access operations for merchant validation."""

import logging
from typing import Dict, List, Optional, Tuple

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from receipt_label.utils import get_client_manager
from receipt_label.utils.client_manager import ClientManager

from receipt_dynamo.entities import (
    Receipt,
    ReceiptLetter,
    ReceiptLine,
    ReceiptMetadata,
    ReceiptWord,
    ReceiptWordLabel,
)

# Initialize logger
logger = logging.getLogger(__name__)


def list_receipt_metadatas(
    client_manager: ClientManager = None,
) -> List[ReceiptMetadata]:
    """
    Lists all receipt metadata entities from the DynamoDB table.

    Returns:
        List[ReceiptMetadata]: All receipt metadata records from DynamoDB

    Raises:
        ClientError: If DynamoDB operation fails

    Example:
        >>> metadatas = list_receipt_metadatas()
        >>> print(f"Found {len(metadatas)} metadata records")
    """
    if client_manager is None:
        client_manager = get_client_manager()
    try:
        result = client_manager.dynamo.listReceiptMetadatas()
        return result[0] if result else []
    except (ClientError, BotoCoreError) as e:
        logger.error("Failed to list receipt metadatas: %s", e)
        raise


def list_receipts_for_merchant_validation(
    client_manager: ClientManager = None,
) -> List[Tuple[str, int]]:
    """
    Lists all receipts that do not have receipt metadata.

    This function efficiently identifies receipts that need merchant validation
    by comparing all receipts against existing metadata records.

    Returns:
        List[Tuple[str, int]]: A list of tuples containing the image_id and
            receipt_id of the receipts that do not have receipt metadata.

    Raises:
        ClientError: If DynamoDB operations fail

    Example:
        >>> pending_receipts = list_receipts_for_merchant_validation()
        >>> for image_id, receipt_id in pending_receipts[:5]:
        ...     print(f"Receipt {receipt_id} in image {image_id} needs validation")
    """
    if client_manager is None:
        client_manager = get_client_manager()
    try:
        receipts, lek = client_manager.dynamo.listReceipts(limit=25)
        while lek:
            next_receipts, lek = client_manager.dynamo.listReceipts(
                limit=25, lastEvaluatedKey=lek
            )
            receipts.extend(next_receipts)
    except (ClientError, BotoCoreError) as e:
        logger.error("Failed to list receipts: %s", e)
        raise
    # Filter out receipts that have receipt metadata
    try:
        receipt_metadatas = client_manager.dynamo.getReceiptMetadatas(
            [
                {
                    "PK": {"S": f"IMAGE#{receipt.image_id}"},
                    "SK": {"S": f"RECEIPT#{receipt.receipt_id:05d}#METADATA"},
                }
                for receipt in receipts
            ]
        )
    except (ClientError, BotoCoreError) as e:
        logger.error("Failed to get receipt metadatas: %s", e)
        raise
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


def get_receipt_details(
    image_id: str, receipt_id: int, client_manager: ClientManager = None
) -> Tuple[
    Receipt,
    List[ReceiptLine],
    List[ReceiptWord],
    List[ReceiptLetter],
    List[ReceiptWordLabel],
]:
    """
    Get a receipt with all its associated details from DynamoDB.

    Retrieves the complete set of receipt data including lines, words, letters,
    and labels needed for merchant validation processing.

    Args:
        image_id: The image ID of the receipt
        receipt_id: The receipt ID

    Returns:
        Tuple containing:
            - Receipt: The main receipt record
            - List[ReceiptLine]: Receipt lines
            - List[ReceiptWord]: Receipt words
            - List[ReceiptLetter]: Receipt letters
            - List[ReceiptWordLabel]: Receipt word labels

    Raises:
        ClientError: If DynamoDB operations fail
        ValueError: If image_id or receipt_id are invalid

    Example:
        >>> receipt, lines, words, letters, labels = get_receipt_details(
        ...     "IMG123", 1
        ... )
        >>> print(f"Receipt has {len(words)} words and {len(lines)} lines")
    """
    if not image_id or not isinstance(image_id, str):
        raise ValueError(f"Invalid image_id: {image_id}")
    if not isinstance(receipt_id, int) or receipt_id < 0:
        raise ValueError(f"Invalid receipt_id: {receipt_id}")

    if client_manager is None:
        client_manager = get_client_manager()

    try:
        receipt = client_manager.dynamo.getReceipt(image_id, receipt_id)
        receipt_lines = client_manager.dynamo.getReceiptLines(
            image_id, receipt_id
        )
        receipt_words = client_manager.dynamo.getReceiptWords(
            image_id, receipt_id
        )
        receipt_letters = client_manager.dynamo.getReceiptLetters(
            image_id, receipt_id
        )
        receipt_word_labels = client_manager.dynamo.getReceiptWordLabels(
            image_id, receipt_id
        )
    except (ClientError, BotoCoreError) as e:
        logger.error(
            f"Failed to get receipt details for {image_id}/{receipt_id}: {e}"
        )
        raise

    return (
        receipt,
        receipt_lines,
        receipt_words,
        receipt_letters,
        receipt_word_labels,
    )


def write_receipt_metadata_to_dynamo(
    metadata: ReceiptMetadata, client_manager: ClientManager = None
) -> None:
    """
    Write receipt metadata to DynamoDB.

    Persists a single ReceiptMetadata record to the database with error handling.

    Args:
        metadata: The ReceiptMetadata object to write

    Raises:
        ValueError: If metadata is invalid
        ClientError: If DynamoDB operation fails

    Example:
        >>> metadata = ReceiptMetadata(
        ...     image_id="IMG123",
        ...     receipt_id=1,
        ...     merchant_name="Test Store"
        ... )
        >>> write_receipt_metadata_to_dynamo(metadata)
    """
    if not metadata:
        raise ValueError("Metadata cannot be None")
    if not hasattr(metadata, "image_id") or not metadata.image_id:
        raise ValueError("Metadata must have a valid image_id")
    if not hasattr(metadata, "receipt_id") or metadata.receipt_id is None:
        raise ValueError("Metadata must have a valid receipt_id")

    if client_manager is None:
        client_manager = get_client_manager()

    try:
        client_manager.dynamo.addReceiptMetadata(metadata)
        logger.debug(
            f"Successfully wrote metadata for {metadata.image_id}/{metadata.receipt_id}"
        )
    except (ClientError, BotoCoreError) as e:
        logger.error(
            f"Failed to write metadata for {metadata.image_id}/{metadata.receipt_id}: {e}"
        )
        raise


def query_records_by_place_id(
    place_id: str, client_manager: ClientManager = None
) -> List[ReceiptMetadata]:
    """
    Query DynamoDB for all ReceiptMetadata records with the given place_id.

    Efficiently finds all receipts associated with a specific Google Places location.

    Args:
        place_id: The Google Places place_id to search for

    Returns:
        List of ReceiptMetadata records with matching place_id

    Raises:
        ValueError: If place_id is invalid
        ClientError: If DynamoDB operations fail

    Example:
        >>> records = query_records_by_place_id("ChIJN1t_tDeuEmsRUsoyG83frY4")
        >>> print(f"Found {len(records)} receipts for this location")
    """
    if not place_id or not isinstance(place_id, str):
        raise ValueError(f"Invalid place_id: {place_id}")

    try:
        all_records = list_receipt_metadatas(client_manager)
        return [
            record for record in all_records if record.place_id == place_id
        ]
    except Exception as e:
        logger.error("Failed to query records by place_id %s: %s", place_id, e)
        raise


def list_all_receipt_metadatas(
    client_manager: ClientManager = None,
) -> Tuple[List[ReceiptMetadata], Dict[str, List[ReceiptMetadata]]]:
    """
    List all receipt metadata records and group them by place_id.

    Provides both the complete list and a dictionary grouped by place_id
    for efficient access patterns in clustering and canonicalization operations.

    Returns:
        Tuple containing:
            - List[ReceiptMetadata]: All metadata records
            - Dict[str, List[ReceiptMetadata]]: Records grouped by place_id

    Raises:
        ClientError: If DynamoDB operations fail

    Example:
        >>> all_records, grouped = list_all_receipt_metadatas()
        >>> print(f"Total records: {len(all_records)}")
        >>> print(f"Unique locations: {len(grouped)}")
    """
    try:
        all_records = list_receipt_metadatas(client_manager)
        records_by_place_id: Dict[str, List[ReceiptMetadata]] = {}

        for record in all_records:
            if record.place_id:
                if record.place_id not in records_by_place_id:
                    records_by_place_id[record.place_id] = []
                records_by_place_id[record.place_id].append(record)

        return all_records, records_by_place_id
    except Exception as e:
        logger.error("Failed to list and group metadata records: %s", e)
        raise


def persist_alias_updates(
    records: List[ReceiptMetadata], client_manager: ClientManager = None
) -> None:
    """
    Persist canonical alias updates to DynamoDB in batches.

    Efficiently updates multiple records while respecting DynamoDB batch limits
    and providing proper error handling for each batch.

    Args:
        records: List of ReceiptMetadata records to update

    Raises:
        ValueError: If records list is invalid
        ClientError: If DynamoDB batch operations fail

    Example:
        >>> updated_records = [metadata1, metadata2, metadata3]
        >>> persist_alias_updates(updated_records)
        >>> print(f"Updated {len(updated_records)} records")
    """
    if not records:
        logger.warning("No records provided for alias updates")
        return

    if not isinstance(records, list):
        raise ValueError("Records must be a list")

    if client_manager is None:
        client_manager = get_client_manager()

    batch_size = 25  # DynamoDB batch write limit
    total_batches = (len(records) + batch_size - 1) // batch_size

    logger.info(
        f"Persisting {len(records)} records in {total_batches} batches"
    )

    for i in range(0, len(records), batch_size):
        batch = records[i : i + batch_size]
        batch_num = (i // batch_size) + 1

        try:
            for record in batch:
                client_manager.dynamo.updateReceiptMetadata(record)
            logger.debug(
                f"Successfully processed batch {batch_num}/{total_batches}"
            )
        except (ClientError, BotoCoreError) as e:
            logger.error(
                f"Failed to process batch {batch_num}/{total_batches}: {e}"
            )
            raise
