"""Lambda handler for consolidating newly validated merchant metadata."""

from logging import INFO, Formatter, StreamHandler, getLogger
from typing import Any, Dict, List, Optional

import botocore.exceptions
from receipt_dynamo.entities import ReceiptMetadata
from receipt_label.merchant_validation import (
    normalize_address,
    query_records_by_place_id,
    update_items_with_canonical,
)
from receipt_label.utils import get_clients

logger = getLogger()
logger.setLevel(INFO)

if len(logger.handlers) == 0:
    handler = StreamHandler()
    handler.setFormatter(
        Formatter(
            "[%(levelname)s] %(asctime)s.%(msecs)dZ %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(handler)

dynamo_client, _, _ = get_clients()


def self_canonize_record(metadata: ReceiptMetadata) -> Dict[str, Any]:
    """
    Create canonical values for a record from its own data.

    Args:
        metadata (ReceiptMetadata): The record to canonize

    Returns:
        Dict[str, Any]: Dictionary with canonical values
    """
    return {
        "canonical_place_id": metadata.place_id,
        "canonical_merchant_name": metadata.merchant_name,
        "canonical_address": normalize_address(metadata.address),
        "canonical_phone_number": metadata.phone_number,
    }


def extract_receipts_from_event(event: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Extract receipts to process from the event.

    Prioritizes validation results over original receipts.

    Args:
        event: The Step Function event

    Returns:
        List of receipt data dictionaries to process
    """
    receipts_to_process = []

    # First try to get data from the validation results
    validation_results = event.get("validationResults", [])
    if validation_results:
        logger.info("Found %s validation results", len(validation_results))
        receipts_to_process.extend(validation_results)
    else:
        # If no validation results, fall back to the original receipts list
        # This is a safeguard in case the output mapping changes
        original_receipts = event.get("receipts", [])
        if original_receipts:
            logger.info(
                "No validation results found, using %s original receipts",
                len(original_receipts),
            )
            receipts_to_process.extend(original_receipts)

    return receipts_to_process


def get_receipt_metadata(
    image_id: str, receipt_id: str
) -> Optional[ReceiptMetadata]:
    """
    Retrieve metadata for a receipt.

    Args:
        image_id: The image ID
        receipt_id: The receipt ID

    Returns:
        ReceiptMetadata object or None if not found
    """
    try:
        return dynamo_client.getReceiptMetadata(
            image_id=image_id, receipt_id=int(receipt_id)
        )
    except ValueError as e:
        # Handle case where metadata doesn't exist
        if "receipt_metadata does not exist" in str(e):
            logger.warning("No metadata found for %s/%s", image_id, receipt_id)
            return None
        # Handle case where receipt_id cannot be converted to int
        if "invalid literal for int()" in str(e):
            logger.error("Invalid receipt_id format: %s", receipt_id)
            return None
        raise


def build_canonical_details_from_record(
    canonical_record: ReceiptMetadata,
) -> Dict[str, Any]:
    """
    Build canonical details dictionary from an existing canonical record.

    Args:
        canonical_record: The canonical record to extract details from

    Returns:
        Dictionary with canonical details
    """
    return {
        "canonical_place_id": (
            canonical_record.canonical_place_id or canonical_record.place_id
        ),
        "canonical_merchant_name": (
            canonical_record.canonical_merchant_name
            or canonical_record.merchant_name
        ),
        "canonical_address": (
            canonical_record.canonical_address
            or normalize_address(canonical_record.address)
        ),
        "canonical_phone_number": (
            canonical_record.canonical_phone_number
            or canonical_record.phone_number
        ),
    }


def determine_canonical_details(
    metadata: ReceiptMetadata, image_id: str, receipt_id: str
) -> Dict[str, Any]:
    """
    Determine canonical details for a metadata record.

    Args:
        metadata: The metadata record to process
        image_id: The image ID (for logging)
        receipt_id: The receipt ID (for logging)

    Returns:
        Dictionary with canonical details
    """
    place_id = metadata.place_id

    if place_id:
        # Try to find existing canonical records with same place_id
        matching_records = query_records_by_place_id(place_id)

        if matching_records:
            # Use canonical data from first matching record
            canonical_record = matching_records[0]
            canonical_details = build_canonical_details_from_record(
                canonical_record
            )
            logger.info(
                "Found existing canonical record for place_id %s", place_id
            )
        else:
            # No existing canonical records found
            # Self-canonize this record
            canonical_details = self_canonize_record(metadata)
            logger.info(
                "No existing canonical records found for place_id %s. "
                "Self-canonizing.",
                place_id,
            )
    else:
        # No place_id, self-canonize
        canonical_details = self_canonize_record(metadata)
        logger.info(
            "No place_id for %s/%s, self-canonizing", image_id, receipt_id
        )

    return canonical_details


def update_metadata_with_canonical(
    metadata: ReceiptMetadata,
    canonical_details: Dict[str, Any],
    image_id: str,
    receipt_id: str,
) -> bool:
    """
    Update a metadata record with canonical details.

    Args:
        metadata: The metadata record to update
        canonical_details: The canonical details to apply
        image_id: The image ID (for logging)
        receipt_id: The receipt ID (for logging)

    Returns:
        True if successfully updated, False otherwise
    """
    updated = update_items_with_canonical([metadata], canonical_details)

    if updated > 0:
        logger.info(
            "Successfully updated %s/%s with canonical data",
            image_id,
            receipt_id,
        )
        return True
    logger.warning(
        "Failed to update %s/%s with canonical data", image_id, receipt_id
    )
    return False


def process_single_receipt(receipt_data: Dict[str, Any]) -> bool:
    """
    Process a single receipt and update it with canonical data.

    Args:
        receipt_data: Dictionary containing receipt information

    Returns:
        True if successfully updated, False otherwise
    """
    # Extract receipt identifiers
    image_id = receipt_data.get("image_id")
    receipt_id = receipt_data.get("receipt_id")

    if not image_id or not receipt_id:
        logger.warning("Missing identifiers in receipt data: %s", receipt_data)
        return False

    try:
        # Get the metadata record
        metadata = get_receipt_metadata(image_id, receipt_id)
        if not metadata:
            return False

        # Determine canonical details
        canonical_details = determine_canonical_details(
            metadata, image_id, receipt_id
        )

        # Update the record with canonical data
        return update_metadata_with_canonical(
            metadata, canonical_details, image_id, receipt_id
        )

    except botocore.exceptions.ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "Unknown")
        logger.error(
            "DynamoDB error processing receipt %s/%s: %s - %s",
            image_id,
            receipt_id,
            error_code,
            e,
        )
        return False
    except (AttributeError, TypeError) as e:
        logger.error(
            "Invalid data structure for receipt %s/%s: %s",
            image_id,
            receipt_id,
            e,
        )
        return False
    except ValueError as e:
        logger.error(
            "Invalid value for receipt %s/%s: %s", image_id, receipt_id, e
        )
        return False


def consolidate_handler(
    event: Dict[str, Any], _context: Any
) -> Dict[str, Any]:
    """
    Lambda handler for incrementally consolidating newly created receipt
    metadata.
    This should be run after ForEachReceipt map state in the step function.

    For each receipt that was processed:
    1. Gets metadata record based on image_id and receipt_id
    2. For records with place_id, looks for existing records with same
       place_id
    3. If found, copies canonical values from matched record to the new record
    4. If not found, self-canonizes the record (sets canonical_* based on
       its own data)

    Args:
        event: The Step Function event containing both:
               1. Original receipts (`receipts`) from ListReceipts
               2. Validation results (`validationResults`) from
                  ForEachReceipt map state
        context: Lambda context

    Returns:
        Dict with status and summary of consolidation
    """
    logger.info(
        "Starting consolidate_new_metadata_handler for Phase 2 incremental "
        "consolidation"
    )

    # Extract processed receipts from event
    receipts_to_process = extract_receipts_from_event(event)

    if not receipts_to_process:
        logger.info("No receipts to consolidate")
        return {
            "statusCode": 200,
            "body": {"message": "No receipts to consolidate", "updated": 0},
        }

    logger.info("Found %s receipts to consolidate", len(receipts_to_process))

    # Process each receipt and count successful updates
    updated_count = sum(
        1
        for receipt_data in receipts_to_process
        if process_single_receipt(receipt_data)
    )

    logger.info(
        "Incremental consolidation complete. Updated %s/%s records.",
        updated_count,
        len(receipts_to_process),
    )

    return {
        "statusCode": 200,
        "body": {
            "message": "Incremental consolidation complete",
            "records_processed": len(receipts_to_process),
            "records_updated": updated_count,
        },
    }
