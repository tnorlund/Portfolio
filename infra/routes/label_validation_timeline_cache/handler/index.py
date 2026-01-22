"""
Lambda handler for generating label validation timeline cache.

This module generates a timeline of label validation counts over time by:
1. Scanning all labels via GSI1 (by label type)
2. Sorting chronologically by timestamp_added
3. Computing cumulative counts at sampled intervals
4. Caching the keyframes to S3 for frontend animation
"""

import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone

import boto3
from receipt_dynamo.constants import CORE_LABELS
from receipt_dynamo.data.dynamo_client import DynamoClient

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Configuration
KEYFRAME_COUNT = 200  # Number of keyframes to sample for smooth animation
S3_CACHE_KEY = "label_validation_timeline.json"

# Initialize clients outside handler for connection reuse
_dynamo_client = None
_s3_client = None


def get_dynamo_client():
    """Get or create DynamoDB client with lazy initialization."""
    global _dynamo_client
    if _dynamo_client is None:
        _dynamo_client = DynamoClient(os.environ["DYNAMODB_TABLE_NAME"])
    return _dynamo_client


def get_s3_client():
    """Get or create S3 client with lazy initialization."""
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3")
    return _s3_client


def fetch_all_labels():
    """Fetch all labels across core label types from DynamoDB.

    Returns:
        List of label records sorted by timestamp_added
    """
    dynamo_client = get_dynamo_client()
    all_labels = []

    for core_label in CORE_LABELS:
        logger.info("Fetching labels for: %s", core_label)
        labels, last_key = dynamo_client.get_receipt_word_labels_by_label(
            label=core_label,
            limit=10000,
        )
        all_labels.extend(labels)

        while last_key:
            labels, last_key = dynamo_client.get_receipt_word_labels_by_label(
                label=core_label,
                limit=10000,
                last_evaluated_key=last_key,
            )
            all_labels.extend(labels)

        logger.info("Fetched %d labels for %s", len(all_labels), core_label)

    # Sort chronologically by timestamp_added
    all_labels.sort(
        key=lambda x: x.timestamp_added if x.timestamp_added else ""
    )

    logger.info("Total labels fetched: %d", len(all_labels))
    return all_labels


def generate_keyframes(all_labels):
    """Generate sampled keyframes with cumulative counts.

    Args:
        all_labels: List of label records sorted by timestamp_added

    Returns:
        List of keyframe dictionaries
    """
    if not all_labels:
        return []

    total = len(all_labels)
    sample_interval = max(1, total // KEYFRAME_COUNT)

    keyframes = []
    # Track cumulative counts: {label_name: {status: count}}
    cumulative = defaultdict(lambda: defaultdict(int))

    for i, label in enumerate(all_labels):
        # Update cumulative counts
        label_name = label.label
        # Handle validation_status as either Enum or string
        if label.validation_status is None:
            status = "NONE"
        elif hasattr(label.validation_status, "value"):
            status = label.validation_status.value
        else:
            status = str(label.validation_status)
        cumulative[label_name][status] += 1

        # Sample keyframe at intervals and always include last record
        if i % sample_interval == 0 or i == total - 1:
            # Build labels snapshot
            labels_snapshot = {}
            for lbl, counts in cumulative.items():
                total_for_label = sum(counts.values())
                labels_snapshot[lbl] = {
                    "VALID": counts.get("VALID", 0),
                    "INVALID": counts.get("INVALID", 0),
                    "PENDING": counts.get("PENDING", 0),
                    "NEEDS_REVIEW": counts.get("NEEDS_REVIEW", 0),
                    "NONE": counts.get("NONE", 0),
                    "total": total_for_label,
                }

            keyframes.append(
                {
                    "progress": i / max(1, total - 1),
                    "timestamp": (
                        label.timestamp_added if label.timestamp_added else ""
                    ),
                    "records_processed": i + 1,
                    "labels": labels_snapshot,
                }
            )

    logger.info("Generated %d keyframes", len(keyframes))
    return keyframes


def write_cache_to_s3(timeline_data):
    """Write timeline cache to S3.

    Args:
        timeline_data: Dictionary containing timeline keyframes
    """
    s3_client = get_s3_client()
    bucket = os.environ["S3_CACHE_BUCKET"]

    s3_client.put_object(
        Bucket=bucket,
        Key=S3_CACHE_KEY,
        Body=json.dumps(timeline_data),
        ContentType="application/json",
    )

    logger.info("Wrote cache to s3://%s/%s", bucket, S3_CACHE_KEY)


def handler(event, _context):
    """AWS Lambda handler for generating label validation timeline cache.

    This handler can be invoked manually or on a schedule to regenerate
    the timeline cache.
    """
    logger.info("Starting label validation timeline cache generation")
    logger.info("Event: %s", event)

    try:
        # Fetch all labels
        all_labels = fetch_all_labels()

        if not all_labels:
            logger.warning("No labels found to process")
            return {
                "statusCode": 200,
                "body": json.dumps(
                    {
                        "message": "No labels found",
                        "total_records": 0,
                    }
                ),
            }

        # Generate keyframes
        keyframes = generate_keyframes(all_labels)

        # Build timeline response
        timeline_data = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_records": len(all_labels),
            "keyframes": keyframes,
        }

        # Write to S3
        write_cache_to_s3(timeline_data)

        logger.info(
            "Cache generation complete: %d records, %d keyframes",
            len(all_labels),
            len(keyframes),
        )

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": "Cache generated successfully",
                    "total_records": len(all_labels),
                    "keyframes_count": len(keyframes),
                    "generated_at": timeline_data["generated_at"],
                }
            ),
        }

    except Exception as e:
        logger.error("Error generating cache: %s", e, exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }
