"""Lambda handler for listing PENDING labels and creating manifest."""

import json
import os
import time
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, Tuple

import boto3
from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import ValidationStatus

# Import EMF metrics utility
# For zip-based Lambda, utils is packaged at the root level
from utils.emf_metrics import emf_metrics  # type: ignore


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Query DynamoDB for labels with specified status, group by receipt, create manifest.

    Args:
        event: Lambda event (optional "limit" key, optional "status" key - defaults to "PENDING")
        context: Lambda context

    Returns:
        {
            "manifest_s3_key": "...",
            "manifest_s3_bucket": "...",
            "execution_id": "...",
            "total_receipts": 100,
            "total_pending_labels": 450,
            "receipt_indices": [0, 1, 2, ..., 99],
            "status": "PENDING" or "INVALID"
        }
    """
    dynamo = DynamoClient(os.environ["DYNAMODB_TABLE_NAME"])
    s3_client = boto3.client("s3")
    bucket = os.environ["S3_BUCKET"]

    # Get execution ID from context (or generate UUID)
    execution_id = context.aws_request_id if context else str(uuid.uuid4())

    # Get status from event (default to PENDING for backward compatibility)
    status_str = event.get("status", "PENDING").upper()
    try:
        validation_status = ValidationStatus[status_str]
    except KeyError as exc:
        valid_statuses = [s.name for s in ValidationStatus]
        raise ValueError(f"Invalid status: {status_str}. Must be one of: {valid_statuses}") from exc

    # Track timing for metrics
    start_time = time.time()

    # Collect metrics during processing
    collected_metrics: Dict[str, float] = {}
    dynamodb_query_count = [0]  # Use list to allow modification in nested function

    try:
        # Query all labels with specified status
        def increment_query_count():
            dynamodb_query_count[0] += 1

        limit = event.get("limit")
        pending_labels = _list_all_labels_with_status(
            dynamo, status=validation_status, limit=limit, query_count_callback=increment_query_count
        )

        # Group by receipt (image_id, receipt_id)
        receipts_dict = _group_labels_by_receipt(pending_labels)

        # Create manifest with receipt identifiers only (no data fetching)
        manifest_receipts = []
        for index, ((image_id, receipt_id), labels) in enumerate(receipts_dict.items()):
            manifest_receipts.append({
                "index": index,
                "image_id": image_id,
                "receipt_id": receipt_id,
                "pending_label_count": len(labels),
            })

        # Create and upload manifest
        manifest = {
            "execution_id": execution_id,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "total_receipts": len(manifest_receipts),
            "receipts": manifest_receipts,
            "status": status_str,  # Include status in manifest so validate_receipt Lambda can read it
        }

        manifest_key = f"pending_labels/{execution_id}/manifest.json"
        s3_client.put_object(
            Bucket=bucket,
            Key=manifest_key,
            Body=json.dumps(manifest).encode("utf-8"),
            ContentType="application/json",
        )

        # Create receipt_indices array for Map state
        receipt_indices = list(range(len(manifest_receipts)))  # [0, 1, 2, ..., N-1]

        # Calculate duration
        duration_ms = (time.time() - start_time) * 1000

        # Calculate statistics
        label_counts_per_receipt = [len(labels) for labels in receipts_dict.values()]
        avg_labels_per_receipt = (
            sum(label_counts_per_receipt) / len(label_counts_per_receipt)
            if label_counts_per_receipt
            else 0
        )
        max_labels_per_receipt = max(label_counts_per_receipt) if label_counts_per_receipt else 0

        # Collect metrics
        collected_metrics.update({
            "LabelsListed": len(pending_labels),
            "ReceiptsWithLabels": len(manifest_receipts),
            "ListPendingLabelsDuration": duration_ms,
            "DynamoDBQueries": dynamodb_query_count[0],
            "ManifestSize": len(manifest_receipts),
        })

        # Properties for detailed analysis
        properties = {
            "execution_id": execution_id,
            "total_pending_labels": len(pending_labels),
            "total_receipts": len(manifest_receipts),
            "avg_labels_per_receipt": avg_labels_per_receipt,
            "max_labels_per_receipt": max_labels_per_receipt,
            "limit": limit if limit else "none",
        }

        # Log all metrics via EMF (single log line, no API calls)
        emf_metrics.log_metrics(
            collected_metrics,
            dimensions=None,
            properties=properties,
        )

        return {
            "manifest_s3_key": manifest_key,
            "manifest_s3_bucket": bucket,
            "execution_id": execution_id,
            "total_receipts": len(manifest_receipts),
            "total_pending_labels": len(pending_labels),
            "receipt_indices": receipt_indices,
            "status": status_str,  # Pass status to next step
        }

    except Exception as e:
        # Log error via EMF
        error_type = type(e).__name__
        emf_metrics.log_metrics(
            {"ListPendingLabelsError": 1},
            dimensions={"error_type": error_type},
            properties={
                "error": str(e),
                "execution_id": execution_id,
            },
        )
        raise


def _list_all_labels_with_status(
    dynamo: DynamoClient, status: ValidationStatus, limit: int | None = None, query_count_callback=None
) -> list:
    """Query DynamoDB for all labels with specified status."""
    labels = []
    last_evaluated_key = None
    query_count = 0

    while True:
        batch_labels, last_evaluated_key = dynamo.list_receipt_word_labels_with_status(
            status=status,
            limit=limit if limit and limit < 1000 else 1000,
            last_evaluated_key=last_evaluated_key,
        )

        query_count += 1
        if query_count_callback:
            query_count_callback()

        labels.extend(batch_labels)

        if not last_evaluated_key or (limit and len(labels) >= limit):
            break

    if limit and len(labels) > limit:
        labels = labels[:limit]

    return labels


def _group_labels_by_receipt(labels: list) -> Dict[Tuple[str, int], list]:
    """Group labels by (image_id, receipt_id)."""
    receipts_dict = defaultdict(list)
    for label in labels:
        key = (label.image_id, label.receipt_id)
        receipts_dict[key].append(label)
    return dict(receipts_dict)

