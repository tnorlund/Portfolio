"""
Lambda handler for storing labels in DynamoDB.

This handler stores the final labels (from patterns and/or GPT) as
ReceiptWordLabel entities in DynamoDB.
"""

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize DynamoDB client
dynamodb = boto3.resource("dynamodb")
table_name = os.environ["DYNAMO_TABLE_NAME"]
table = dynamodb.Table(table_name)


def create_label_item(
    receipt_id: str,
    word_id: str,
    label: str,
    confidence: float,
    source: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create a ReceiptWordLabel DynamoDB item."""
    timestamp = datetime.utcnow().isoformat()

    item = {
        "PK": f"RECEIPT#{receipt_id}",
        "SK": f"LABEL#{word_id}#{label}",
        "receipt_id": receipt_id,
        "word_id": word_id,
        "label": label,
        "confidence": confidence,
        "source": source,
        "created_at": timestamp,
        "updated_at": timestamp,
        "TYPE": "ReceiptWordLabel",
    }

    if metadata:
        item["metadata"] = metadata

    return item


def batch_write_labels(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Write labels to DynamoDB in batches."""
    success_count = 0
    error_count = 0

    # DynamoDB batch write limit is 25 items
    batch_size = 25

    for i in range(0, len(items), batch_size):
        batch = items[i : i + batch_size]

        try:
            with table.batch_writer() as batch_writer:
                for item in batch:
                    batch_writer.put_item(Item=item)
                success_count += len(batch)
        except ClientError as e:
            logger.error("Error writing batch: %s", e)
            error_count += len(batch)

    return {
        "success_count": success_count,
        "error_count": error_count,
        "total_items": len(items),
    }


def lambda_handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    """
    Store labels in DynamoDB.

    Args:
        event: Contains receipt_id, pattern_labels, and optional gpt_labels
        context: Lambda context

    Returns:
        Dictionary with:
        - labels_stored: Number of labels successfully stored
        - errors: Number of errors encountered
        - label_summary: Summary of labels by type
    """
    try:
        receipt_id = event["receipt_id"]
        pattern_labels = event.get("labeling", {}).get("pattern_labels", {})
        gpt_labels = event.get("gpt_labels", {})
        batch_info = event.get("batch", {})

        logger.info("Storing labels for receipt: %s", receipt_id)

        items_to_write = []
        label_summary = {}

        # Process pattern-based labels
        for word_id, label_info in pattern_labels.items():
            label = label_info["label"]
            confidence = label_info.get("confidence", 0.95)
            source = label_info.get("source", "pattern_detection")

            item = create_label_item(
                receipt_id=receipt_id,
                word_id=word_id,
                label=label,
                confidence=confidence,
                source=source,
                metadata={
                    "pattern_type": label_info.get("pattern_type"),
                    "match_value": label_info.get("match_value"),
                },
            )
            items_to_write.append(item)

            # Update summary
            label_summary[label] = label_summary.get(label, 0) + 1

        # Process GPT labels (if any)
        for word_id, label in gpt_labels.items():
            # Skip if already labeled by pattern
            if word_id in pattern_labels:
                continue

            item = create_label_item(
                receipt_id=receipt_id,
                word_id=word_id,
                label=label,
                confidence=0.85,  # Default GPT confidence
                source="gpt",
                metadata={
                    "batch_id": batch_info.get("batch_id"),
                    "model": "gpt-4",
                },
            )
            items_to_write.append(item)

            # Update summary
            label_summary[label] = label_summary.get(label, 0) + 1

        # Write labels to DynamoDB
        write_result = batch_write_labels(items_to_write)

        # Update receipt metadata with labeling status
        metadata_update = {
            "PK": f"RECEIPT#{receipt_id}",
            "SK": f"METADATA#{receipt_id}",
            "labeling_status": "completed",
            "labels_applied": len(items_to_write),
            "label_summary": label_summary,
            "labeling_completed_at": datetime.utcnow().isoformat(),
            "pattern_labels_count": len(pattern_labels),
            "gpt_labels_count": len(gpt_labels),
        }

        table.update_item(
            Key={"PK": metadata_update["PK"], "SK": metadata_update["SK"]},
            UpdateExpression="SET labeling_status = :status, labels_applied = :count, "
            "label_summary = :summary, labeling_completed_at = :timestamp, "
            "pattern_labels_count = :pattern_count, gpt_labels_count = :gpt_count",
            ExpressionAttributeValues={
                ":status": "completed",
                ":count": len(items_to_write),
                ":summary": label_summary,
                ":timestamp": datetime.utcnow().isoformat(),
                ":pattern_count": len(pattern_labels),
                ":gpt_count": len(gpt_labels),
            },
        )

        result = {
            "labels_stored": write_result["success_count"],
            "errors": write_result["error_count"],
            "label_summary": label_summary,
            "total_labels": len(items_to_write),
            "pattern_labels": len(pattern_labels),
            "gpt_labels": len(gpt_labels),
        }

        logger.info("Label storage complete: %s", json.dumps(result))
        return result

    except Exception as e:
        logger.error("Error storing labels: %s", str(e))
        raise
