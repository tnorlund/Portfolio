"""
Prepare Labels Handler (Zip Lambda)

Queries DynamoDB for labels of a specific type, groups by merchant,
and uploads batch files to S3 for processing by the container Lambda.
"""

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional

import boto3

from receipt_dynamo import DynamoClient

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Prepare labels for a single label type.

    1. Query DynamoDB for all labels of this type using GSI1
    2. Batch fetch merchant names and word text
    3. Group by merchant
    4. Upload to S3 as NDJSON files

    Input:
    {
        "label_type": "GRAND_TOTAL",
        "execution_id": "abc123",
        "batch_bucket": "bucket-name",
        "max_merchants": null  // Optional limit
    }

    Output:
    {
        "label_type": "GRAND_TOTAL",
        "s3_batch_path": "s3://bucket/batches/...",
        "merchant_groups": [...],
        "total_labels": 1234,
        "total_merchants": 23
    }
    """
    label_type = event["label_type"]
    execution_id = event["execution_id"]
    batch_bucket = event.get("batch_bucket") or os.environ["BATCH_BUCKET"]
    max_merchants: Optional[int] = event.get("max_merchants")
    table_name = os.environ["DYNAMODB_TABLE_NAME"]

    logger.info(
        f"Preparing labels for {label_type}, execution_id={execution_id}"
    )

    dynamo = DynamoClient(table_name)

    # Prepare S3 path
    s3_prefix = f"batches/{execution_id}/{label_type}/"

    # Stream labels from DynamoDB using GSI1
    merchant_groups: Dict[str, list] = {}
    labels_processed = 0
    metadata_cache: Dict[str, str] = {}  # Cache merchant names

    try:
        # Use the GSI1-based query for efficient label retrieval
        # Handle pagination - get_receipt_word_labels_by_label returns a tuple
        last_evaluated_key = None
        while True:
            labels_batch, last_evaluated_key = (
                dynamo.get_receipt_word_labels_by_label(
                    label_type,
                    limit=1000,
                    last_evaluated_key=last_evaluated_key,
                )
            )

            for label in labels_batch:
                labels_processed += 1

                # Get merchant name (with caching)
                # ReceiptWordLabel is a dataclass, use attribute access
                cache_key = f"{label.image_id}#{label.receipt_id}"
                if cache_key not in metadata_cache:
                    try:
                        place = dynamo.get_receipt_place(
                            image_id=label.image_id,
                            receipt_id=label.receipt_id,
                        )
                        metadata_cache[cache_key] = (
                            place.merchant_name if place else "Unknown"
                        )
                    except Exception as e:
                        logger.warning(
                            f"Failed to get place for {cache_key}: {e}"
                        )
                        metadata_cache[cache_key] = "Unknown"

                merchant_name = metadata_cache[cache_key]

                # Check merchant limit
                if max_merchants and merchant_name not in merchant_groups:
                    if len(merchant_groups) >= max_merchants:
                        # Skip this label if we've hit the merchant limit
                        continue

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

                # Group by merchant
                if merchant_name not in merchant_groups:
                    merchant_groups[merchant_name] = []

                merchant_groups[merchant_name].append(
                    {
                        "image_id": label.image_id,
                        "receipt_id": label.receipt_id,
                        "line_id": label.line_id,
                        "word_id": label.word_id,
                        "label": label.label,
                        "validation_status": label.validation_status
                        or "PENDING",
                        "word_text": word_text,
                        "merchant_name": merchant_name,
                    }
                )

                # Log progress every 1000 labels
                if labels_processed % 1000 == 0:
                    logger.info(
                        f"Processed {labels_processed} labels, "
                        f"{len(merchant_groups)} merchants"
                    )

            # Break if no more pages
            if last_evaluated_key is None:
                break

    except Exception as e:
        logger.error(f"Error querying labels: {e}")
        raise

    logger.info(
        f"Finished querying: {labels_processed} labels, "
        f"{len(merchant_groups)} merchants"
    )

    # Upload each merchant group as NDJSON
    # For large groups, split into multiple batches for parallel processing
    # Each batch should complete within Lambda's 15-minute timeout:
    # - ~100 labels per batch
    # - ~50% may need LLM checks (non-VALID)
    # - ~50 LLM calls × 5s each = ~250s with some parallelism
    # - Plus ChromaDB queries and context fetching
    # - Target: < 10 minutes per batch to leave buffer
    MAX_LABELS_PER_BATCH = 100
    output_groups = []
    for merchant_name, labels in merchant_groups.items():
        # Create safe filename base
        safe_name = (
            merchant_name.lower()
            .replace(" ", "-")
            .replace("/", "-")
            .replace("'", "")
            .replace('"', "")
        )
        # Limit filename length
        if len(safe_name) > 50:
            safe_name = safe_name[:50]

        # Split large groups into multiple batches
        if len(labels) > MAX_LABELS_PER_BATCH:
            num_batches = (
                len(labels) + MAX_LABELS_PER_BATCH - 1
            ) // MAX_LABELS_PER_BATCH
            logger.info(
                f"Splitting {merchant_name} ({len(labels)} labels) into {num_batches} batches "
                f"for parallel processing"
            )

            for batch_idx in range(num_batches):
                start_idx = batch_idx * MAX_LABELS_PER_BATCH
                end_idx = min(start_idx + MAX_LABELS_PER_BATCH, len(labels))
                batch_labels = labels[start_idx:end_idx]

                # Create batch-specific filename
                file_key = (
                    f"{s3_prefix}{safe_name}-batch{batch_idx + 1}.ndjson"
                )

                # Convert to NDJSON
                ndjson_content = "\n".join(
                    json.dumps(label) for label in batch_labels
                )

                # Upload to S3
                try:
                    s3.put_object(
                        Bucket=batch_bucket,
                        Key=file_key,
                        Body=ndjson_content.encode("utf-8"),
                        ContentType="application/x-ndjson",
                    )
                    logger.info(
                        f"Uploaded batch {batch_idx + 1}/{num_batches} "
                        f"({len(batch_labels)} labels) to s3://{batch_bucket}/{file_key}"
                    )
                except Exception as e:
                    logger.error(f"Failed to upload {file_key}: {e}")
                    raise

                output_groups.append(
                    {
                        "merchant_name": merchant_name,
                        "batch_file": file_key,
                        "label_type": label_type,
                        "batch_index": batch_idx,
                        "total_batches": num_batches,
                    }
                )
        else:
            # Small group: single batch
            file_key = f"{s3_prefix}{safe_name}.ndjson"

            # Convert to NDJSON
            ndjson_content = "\n".join(json.dumps(label) for label in labels)

            # Upload to S3
            try:
                s3.put_object(
                    Bucket=batch_bucket,
                    Key=file_key,
                    Body=ndjson_content.encode("utf-8"),
                    ContentType="application/x-ndjson",
                )
                logger.info(
                    f"Uploaded {len(labels)} labels to s3://{batch_bucket}/{file_key}"
                )
            except Exception as e:
                logger.error(f"Failed to upload {file_key}: {e}")
                raise

            output_groups.append(
                {
                    "merchant_name": merchant_name,
                    "batch_file": file_key,
                    "label_type": label_type,
                }
            )

    # Write manifest to S3 to avoid Step Functions payload size limits
    manifest_key = f"{s3_prefix}manifest.json"
    manifest_content = json.dumps(output_groups, default=str)
    s3.put_object(
        Bucket=batch_bucket,
        Key=manifest_key,
        Body=manifest_content.encode("utf-8"),
        ContentType="application/json",
    )
    logger.info(
        f"Uploaded manifest with {len(output_groups)} merchant groups to s3://{batch_bucket}/{manifest_key}"
    )

    # Return only the S3 path to the manifest (minimal payload)
    result = {
        "manifest_s3_key": manifest_key,
        "label_type": label_type,
        "total_labels": labels_processed,
        "total_merchants": len(merchant_groups),
    }

    logger.info(
        f"Preparation complete: {len(output_groups)} merchants, {labels_processed} labels"
    )
    return result
