from logging import INFO, Formatter, StreamHandler, getLogger
from typing import Any, Dict

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

    # Extract processed receipts from event - prioritize validationResults
    # over original receipts. The validationResults will contain the data
    # returned from the validate handler
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

    if not receipts_to_process:
        logger.info("No receipts to consolidate")
        return {
            "statusCode": 200,
            "body": {"message": "No receipts to consolidate", "updated": 0},
        }

    logger.info("Found %s receipts to consolidate", len(receipts_to_process))
    updated_count = 0

    for receipt_data in receipts_to_process:
        try:
            # Extract receipt identifiers
            image_id = receipt_data.get("image_id")
            receipt_id = receipt_data.get("receipt_id")

            if not image_id or not receipt_id:
                logger.warning(
                    "Missing identifiers in receipt data: %s", receipt_data
                )
                continue

            # Get the metadata record using dynamo_client method
            try:
                metadata = dynamo_client.getReceiptMetadata(
                    image_id=image_id, receipt_id=receipt_id
                )
            except ValueError as e:
                # Handle case where metadata doesn't exist
                if "receipt_metadata does not exist" in str(e):
                    logger.warning(
                        "No metadata found for %s/%s", image_id, receipt_id
                    )
                    continue
                # Re-raise other ValueError exceptions
                raise

            # Check if place_id exists
            place_id = metadata.place_id
            if place_id:
                # Try to find existing canonical records with same place_id
                matching_records = query_records_by_place_id(place_id)

                if matching_records:
                    # Use canonical data from first matching record
                    canonical_record = matching_records[0]
                    canonical_details = {
                        "canonical_place_id": (
                            canonical_record.canonical_place_id
                            or canonical_record.place_id
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
                    logger.info(
                        "Found existing canonical record for place_id %s",
                        place_id,
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
                    "No place_id for %s/%s, self-canonizing",
                    image_id,
                    receipt_id,
                )

            # Update the record with canonical data
            # Use update_items_with_canonical with a single-element list for
            # consistency with batch processes
            updated = update_items_with_canonical(
                [metadata], canonical_details
            )
            if updated > 0:
                updated_count += 1
                logger.info(
                    "Successfully updated %s/%s with canonical data",
                    image_id,
                    receipt_id,
                )
            else:
                logger.warning(
                    "Failed to update %s/%s with canonical data",
                    image_id,
                    receipt_id,
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
            continue
        except (AttributeError, TypeError) as e:
            logger.error(
                "Invalid data structure for receipt %s/%s: %s",
                image_id,
                receipt_id,
                e,
            )
            continue
        except ValueError as e:
            logger.error(
                "Invalid value for receipt %s/%s: %s", image_id, receipt_id, e
            )
            continue

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
