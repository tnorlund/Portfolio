"""Lambda handler for listing ReceiptMetadata entities and creating manifest."""

import json
import os
import time
import uuid
from datetime import datetime
from typing import Any, Dict

import boto3
from receipt_dynamo import DynamoClient

# Import EMF metrics utility
# For zip-based Lambda, utils is packaged at the root level
from utils.emf_metrics import emf_metrics  # type: ignore


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Query DynamoDB for all ReceiptMetadata entities, create manifest.

    Args:
        event: Lambda event (optional "limit" key)
        context: Lambda context

    Returns:
        {
            "manifest_s3_key": "...",
            "manifest_s3_bucket": "...",
            "execution_id": "...",
            "total_receipts": 451,
            "receipt_indices": [0, 1, 2, ..., 450]
        }
    """
    dynamo = DynamoClient(os.environ["DYNAMODB_TABLE_NAME"])
    s3_client = boto3.client("s3")
    bucket = os.environ["S3_BUCKET"]

    # Get execution ID from context (or generate UUID)
    execution_id = context.aws_request_id if context else str(uuid.uuid4())

    # Track timing for metrics
    start_time = time.time()

    # Collect metrics during processing
    collected_metrics: Dict[str, float] = {}
    dynamodb_query_count = [0]  # Use list to allow modification in nested function

    try:
        # Query all ReceiptMetadata entities
        def increment_query_count():
            dynamodb_query_count[0] += 1

        limit = event.get("limit")
        metadatas = _list_all_metadatas(dynamo, limit=limit, query_count_callback=increment_query_count)

        # Create manifest with receipt identifiers
        manifest_receipts = []
        for index, metadata in enumerate(metadatas):
            manifest_receipts.append({
                "index": index,
                "image_id": metadata.image_id,
                "receipt_id": metadata.receipt_id,
                "merchant_name": metadata.merchant_name,
            })

        # Create and upload manifest
        manifest = {
            "execution_id": execution_id,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "total_receipts": len(manifest_receipts),
            "receipts": manifest_receipts,
        }

        manifest_key = f"metadata_validation/{execution_id}/manifest.json"
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

        # Collect metrics
        collected_metrics.update({
            "MetadataListed": len(metadatas),
            "ReceiptsWithMetadata": len(manifest_receipts),
            "ListMetadataDuration": duration_ms,
            "DynamoDBQueries": dynamodb_query_count[0],
            "ManifestSize": len(manifest_receipts),
        })

        # Properties for detailed analysis
        properties = {
            "execution_id": execution_id,
            "total_metadata": len(metadatas),
            "total_receipts": len(manifest_receipts),
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
            "receipt_indices": receipt_indices,
        }

    except Exception as e:
        # Log error via EMF
        error_type = type(e).__name__
        emf_metrics.log_metrics(
            {"ListMetadataError": 1},
            dimensions={"error_type": error_type},
            properties={
                "error": str(e),
                "execution_id": execution_id,
            },
        )
        raise


def _list_all_metadatas(
    dynamo: DynamoClient, limit: int | None = None, query_count_callback=None
) -> list:
    """Query DynamoDB for all ReceiptMetadata entities."""
    metadatas = []
    last_evaluated_key = None
    query_count = 0

    while True:
        batch, last_evaluated_key = dynamo.list_receipt_metadatas(
            limit=limit if limit and limit < 1000 else 1000,
            last_evaluated_key=last_evaluated_key,
        )

        query_count += 1
        if query_count_callback:
            query_count_callback()

        metadatas.extend(batch)

        if not last_evaluated_key or (limit and len(metadatas) >= limit):
            break

    if limit and len(metadatas) > limit:
        metadatas = metadatas[:limit]

    return metadatas

