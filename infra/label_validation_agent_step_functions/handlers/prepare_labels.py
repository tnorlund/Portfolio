"""
Prepare Labels Handler (Zip Lambda)

Queries DynamoDB for NEEDS_REVIEW labels, optionally filtered by CORE_LABEL(s),
and splits them into batches for processing by the container Lambda.
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

import boto3

from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import ValidationStatus

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")

# Maximum labels per batch (to stay within Lambda timeout)
# Each label takes ~5-10s with agent (LLM + tools)
# Target: ~50 labels per batch = ~5-8 minutes
MAX_LABELS_PER_BATCH = 50


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Prepare NEEDS_REVIEW labels for validation.

    1. Query DynamoDB for NEEDS_REVIEW labels
    2. Optionally filter by label_types (CORE_LABEL(s))
    3. Split into batches for parallel processing
    4. Upload to S3 as NDJSON files

    Input:
    {
        "execution_id": "abc123",
        "batch_bucket": "bucket-name",
        "label_types": ["MERCHANT_NAME"],  // Optional: filter by CORE_LABEL(s), null = all
        "max_labels": 1000,  // Optional: limit total number of labels to process
        "validation_statuses": ["NEEDS_REVIEW", "VALID", "INVALID", "PENDING"]  // Optional: filter by status
    }

    Output:
    {
        "manifest_s3_key": "batches/abc123/manifest.json",
        "total_labels": 1234,
        "total_batches": 25
    }
    """
    execution_id = event["execution_id"]
    batch_bucket = event.get("batch_bucket") or os.environ["BATCH_BUCKET"]
    label_types: Optional[List[str]] = event.get("label_types")  # Optional filter
    max_labels: Optional[int] = event.get("max_labels")  # Optional limit
    validation_statuses_input: Optional[List[str]] = event.get(
        "validation_statuses"
    )
    table_name = os.environ["DYNAMODB_TABLE_NAME"]

    logger.info(
        f"Preparing NEEDS_REVIEW labels, execution_id={execution_id}, "
        f"label_types={label_types or 'all'}, max_labels={max_labels or 'unlimited'}"
    )

    dynamo = DynamoClient(table_name)

    # Normalize validation statuses; default to NEEDS_REVIEW
    if validation_statuses_input:
        validation_statuses: List[Any] = []
        for status_value in validation_statuses_input:
            try:
                validation_statuses.append(ValidationStatus(status_value))
            except Exception:
                validation_statuses.append(status_value)
    else:
        validation_statuses = [ValidationStatus.NEEDS_REVIEW]

    # Prepare S3 path
    s3_prefix = f"batches/{execution_id}/"

    # Query NEEDS_REVIEW labels
    labels: List[Dict[str, Any]] = []
    labels_processed = 0
    metadata_cache: Dict[str, str] = {}  # Cache merchant names

    try:
        # Query by validation_status (one or many)
        for validation_status in validation_statuses:
            last_evaluated_key = None
            while True:
                labels_batch, last_evaluated_key = dynamo.list_receipt_word_labels_with_status(
                    status=validation_status,
                    limit=1000,
                    last_evaluated_key=last_evaluated_key,
                )

                for label in labels_batch:
                    # Filter by label_types if provided
                    if label_types and label.label not in label_types:
                        continue

                    # Check max_labels limit
                    if max_labels and labels_processed >= max_labels:
                        logger.info(
                            f"Reached max_labels limit ({max_labels}), stopping"
                        )
                        break

                    labels_processed += 1

                    # Get merchant name (with caching)
                    cache_key = f"{label.image_id}#{label.receipt_id}"
                    if cache_key not in metadata_cache:
                        try:
                            metadata = dynamo.get_receipt_metadata(
                                image_id=label.image_id,
                                receipt_id=label.receipt_id,
                            )
                            metadata_cache[cache_key] = (
                                metadata.merchant_name if metadata else None
                            )
                        except Exception as e:
                            logger.warning(
                                f"Failed to get metadata for {cache_key}: {e}"
                            )
                            metadata_cache[cache_key] = None

                    merchant_name = metadata_cache[cache_key]

                    # Get word text
                    try:
                        word = dynamo.get_receipt_word(
                            receipt_id=label.receipt_id,
                            image_id=label.image_id,
                            line_id=label.line_id,
                            word_id=label.word_id,
                        )
                        word_text = word.text if word else ""
                    except Exception as e:
                        logger.warning(
                            f"Failed to get word for {label.image_id}#"
                            f"{label.receipt_id}#{label.line_id}#"
                            f"{label.word_id}: {e}"
                        )
                        word_text = ""

                    labels.append(
                        {
                            "image_id": label.image_id,
                            "receipt_id": label.receipt_id,
                            "line_id": label.line_id,
                            "word_id": label.word_id,
                            "label": label.label,
                            "validation_status": (
                                label.validation_status or "NEEDS_REVIEW"
                            ),
                            "word_text": word_text,
                            "merchant_name": merchant_name,
                            "reasoning": label.reasoning or "",
                        }
                    )

                    # Log progress every 1000 labels
                    if labels_processed % 1000 == 0:
                        logger.info(f"Processed {labels_processed} labels")

                    # Break if we've reached max_labels limit
                    if max_labels and labels_processed >= max_labels:
                        logger.info(
                            f"Reached max_labels limit ({max_labels}), stopping"
                        )
                        break

                # Break if no more pages or we've reached max_labels
                if last_evaluated_key is None or (
                    max_labels and labels_processed >= max_labels
                ):
                    break

            # Exit early if max reached across statuses
            if max_labels and labels_processed >= max_labels:
                break

    except Exception as e:
        logger.error(f"Error querying labels: {e}")
        raise

    logger.info(f"Finished querying: {labels_processed} labels")

    # Split into batches
    batches = []
    for i in range(0, len(labels), MAX_LABELS_PER_BATCH):
        batch_labels = labels[i : i + MAX_LABELS_PER_BATCH]
        batch_idx = i // MAX_LABELS_PER_BATCH

        # Create batch file
        file_key = f"{s3_prefix}batch{batch_idx + 1}.ndjson"

        # Convert to NDJSON
        ndjson_content = "\n".join(json.dumps(label) for label in batch_labels)

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
                f"({len(batch_labels)} labels) to s3://{batch_bucket}/{file_key}"
            )
        except Exception as e:
            logger.error(f"Failed to upload {file_key}: {e}")
            raise

        batches.append(
            {
                "batch_file": file_key,
                "batch_index": batch_idx,
                "label_count": len(batch_labels),
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
        "total_labels": labels_processed,
        "total_batches": len(batches),
    }

    logger.info(f"Preparation complete: {len(batches)} batches, {labels_processed} labels")
    return result

