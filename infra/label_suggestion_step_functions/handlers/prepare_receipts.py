"""
Prepare Receipts Handler (Zip Lambda)

Queries DynamoDB for receipts with unlabeled words and splits them into batches
for processing by the container Lambda.
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional, Set, Tuple

import boto3

from receipt_dynamo import DynamoClient

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")

# Maximum receipts per batch (to stay within Lambda timeout)
# Each receipt takes ~20-30s with agent (ChromaDB queries + optional LLM)
# Target: ~10 receipts per batch = ~3-5 minutes
MAX_RECEIPTS_PER_BATCH = 10


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Prepare receipts with unlabeled words for label suggestion.

    1. Query DynamoDB for all receipts
    2. For each receipt, check if it has unlabeled words (excluding noise)
    3. Split into batches for parallel processing
    4. Upload to S3 as NDJSON files

    Input:
    {
        "execution_id": "abc123",
        "batch_bucket": "bucket-name",
        "max_receipts": 100  // Optional: limit total number of receipts to process
    }

    Output:
    {
        "manifest_s3_key": "batches/abc123/manifest.json",
        "total_receipts": 50,
        "total_batches": 5
    }
    """
    execution_id = event["execution_id"]
    batch_bucket = event.get("batch_bucket") or os.environ["BATCH_BUCKET"]
    max_receipts: Optional[int] = event.get("max_receipts")  # Optional limit
    table_name = os.environ["DYNAMODB_TABLE_NAME"]

    logger.info(
        f"Preparing receipts with unlabeled words, execution_id={execution_id}, "
        f"max_receipts={max_receipts or 'unlimited'}"
    )

    dynamo = DynamoClient(table_name)

    # Prepare S3 path
    s3_prefix = f"batches/{execution_id}/"

    # Find receipts with unlabeled words
    receipts: List[Dict[str, Any]] = []
    receipts_processed = 0
    receipts_checked = 0

    try:
        # Get all unique receipt keys (image_id, receipt_id)
        # We'll scan for RECEIPT items or use a more efficient method
        receipt_keys: Set[Tuple[str, int]] = set()

        # Get all receipt keys by listing receipt metadata
        # This gives us all (image_id, receipt_id) pairs
        logger.info("Scanning for receipts...")
        last_evaluated_key = None
        while True:
            # List receipt metadata to get all receipt keys
            all_metadata, last_evaluated_key = dynamo.list_receipt_metadatas(
                limit=1000,
                last_evaluated_key=last_evaluated_key,
            )

            for metadata in all_metadata:
                receipt_keys.add((metadata.image_id, metadata.receipt_id))

            if not last_evaluated_key:
                break

        logger.info(f"Found {len(receipt_keys)} unique receipts to check")

        # Check each receipt for unlabeled words
        for image_id, receipt_id in receipt_keys:
            if max_receipts and receipts_processed >= max_receipts:
                logger.info(f"Reached max_receipts limit ({max_receipts}), stopping")
                break

            receipts_checked += 1
            if receipts_checked % 100 == 0:
                logger.info(
                    f"Checked {receipts_checked} receipts, found {receipts_processed} with unlabeled words"
                )

            try:
                # Get all words (excluding noise)
                words = dynamo.list_receipt_words_from_receipt(
                    image_id=image_id,
                    receipt_id=receipt_id,
                )
                meaningful_words = [
                    w for w in words if not getattr(w, "is_noise", False)
                ]

                if not meaningful_words:
                    continue  # Skip receipts with no meaningful words

                # Get existing labels
                existing_labels, _ = dynamo.list_receipt_word_labels_for_receipt(
                    image_id=image_id,
                    receipt_id=receipt_id,
                )

                # Find unlabeled words
                labeled_word_keys = {(l.line_id, l.word_id) for l in existing_labels}
                unlabeled_words = [
                    {
                        "word_id": w.word_id,
                        "line_id": w.line_id,
                        "text": w.text,
                    }
                    for w in meaningful_words
                    if (w.line_id, w.word_id) not in labeled_word_keys
                ]

                # Only include receipts with unlabeled words
                if unlabeled_words:
                    # Get merchant metadata
                    metadata = dynamo.get_receipt_metadata(
                        image_id=image_id,
                        receipt_id=receipt_id,
                    )
                    merchant_name = metadata.merchant_name if metadata else None

                    receipts.append(
                        {
                            "image_id": image_id,
                            "receipt_id": receipt_id,
                            "merchant_name": merchant_name,
                            "unlabeled_words_count": len(unlabeled_words),
                            "total_words": len(meaningful_words),
                            "existing_labels_count": len(existing_labels),
                        }
                    )
                    receipts_processed += 1

            except Exception as e:
                logger.warning(f"Error checking receipt {image_id}#{receipt_id}: {e}")
                continue

    except Exception as e:
        logger.error(f"Error querying receipts: {e}")
        raise

    logger.info(
        f"Finished checking receipts: {receipts_checked} checked, "
        f"{receipts_processed} with unlabeled words"
    )

    if not receipts:
        logger.info("No receipts with unlabeled words found")
        return {
            "manifest_s3_key": None,
            "total_receipts": 0,
            "total_batches": 0,
        }

    # Split into batches
    batches = []
    for i in range(0, len(receipts), MAX_RECEIPTS_PER_BATCH):
        batch_receipts = receipts[i : i + MAX_RECEIPTS_PER_BATCH]
        batch_idx = i // MAX_RECEIPTS_PER_BATCH

        # Create batch file
        file_key = f"{s3_prefix}batch{batch_idx + 1}.ndjson"

        # Convert to NDJSON
        ndjson_content = "\n".join(json.dumps(receipt) for receipt in batch_receipts)

        # Upload to S3
        try:
            s3.put_object(
                Bucket=batch_bucket,
                Key=file_key,
                Body=ndjson_content.encode("utf-8"),
                ContentType="application/x-ndjson",
            )
            logger.info(
                f"Uploaded batch {batch_idx + 1} "
                f"({len(batch_receipts)} receipts) to s3://{batch_bucket}/{file_key}"
            )
        except Exception as e:
            logger.error(f"Failed to upload {file_key}: {e}")
            raise

        batches.append(
            {
                "batch_file": file_key,
                "batch_index": batch_idx,
                "receipt_count": len(batch_receipts),
            }
        )

    # Write manifest to S3
    manifest_key = f"{s3_prefix}manifest.json"
    manifest_content = json.dumps(batches, default=str)
    s3.put_object(
        Bucket=batch_bucket,
        Key=manifest_key,
        Body=manifest_content.encode("utf-8"),
        ContentType="application/json",
    )
    logger.info(
        f"Uploaded manifest with {len(batches)} batches to s3://{batch_bucket}/{manifest_key}"
    )

    # Return only the S3 path to the manifest (minimal payload)
    result = {
        "manifest_s3_key": manifest_key,
        "total_receipts": receipts_processed,
        "total_batches": len(batches),
    }

    logger.info(
        f"Preparation complete: {len(batches)} batches, {receipts_processed} receipts"
    )
    return result
