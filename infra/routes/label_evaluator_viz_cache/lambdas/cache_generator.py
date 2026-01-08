"""Cache generator for Label Evaluator Visualization.

This Lambda generates viz-sample-data.json from LangSmith bulk exports,
combining trace data (evaluations, decisions, timing) with DynamoDB data
(word bboxes, labels, CDN keys).
"""

import json
import logging
import os
import random
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError
from receipt_dynamo import DynamoClient
from receipt_langsmith import find_visualization_receipts_from_parquet

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment variables
S3_CACHE_BUCKET = os.environ.get("S3_CACHE_BUCKET")
LANGSMITH_EXPORT_BUCKET = os.environ.get("LANGSMITH_EXPORT_BUCKET")
LANGSMITH_EXPORT_PREFIX = os.environ.get("LANGSMITH_EXPORT_PREFIX", "traces/")
BATCH_BUCKET = os.environ.get("BATCH_BUCKET")  # Step Function results bucket
DYNAMODB_TABLE_NAME = os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable")
CACHE_KEY = "viz-sample-data.json"
MAX_RECEIPTS = 10  # Number of receipts to include in visualization cache

if not S3_CACHE_BUCKET:
    logger.error("S3_CACHE_BUCKET environment variable not set")
if not LANGSMITH_EXPORT_BUCKET:
    logger.error("LANGSMITH_EXPORT_BUCKET environment variable not set")

s3_client = boto3.client("s3")
_dynamo_client = None


def _get_dynamo_client() -> DynamoClient:
    """Get or create DynamoDB client."""
    global _dynamo_client
    if _dynamo_client is None:
        _dynamo_client = DynamoClient(table_name=DYNAMODB_TABLE_NAME)
    return _dynamo_client


def _get_receipt_from_dynamodb(image_id: str, receipt_id: int) -> dict[str, Any] | None:
    """Fetch receipt info including CDN keys from DynamoDB."""
    try:
        client = _get_dynamo_client()
        receipt = client.get_receipt(image_id, receipt_id)
        if receipt:
            return {
                "cdn_s3_key": receipt.cdn_s3_key or "",
                "cdn_webp_s3_key": receipt.cdn_webp_s3_key,
                "cdn_avif_s3_key": receipt.cdn_avif_s3_key,
                "cdn_medium_s3_key": receipt.cdn_medium_s3_key,
                "cdn_medium_webp_s3_key": receipt.cdn_medium_webp_s3_key,
                "cdn_medium_avif_s3_key": receipt.cdn_medium_avif_s3_key,
                "width": receipt.width or 0,
                "height": receipt.height or 0,
            }
    except Exception as e:
        logger.warning("Failed to fetch receipt from DynamoDB: %s", e)
    return None


def _get_words_from_dynamodb(image_id: str, receipt_id: int) -> list[dict[str, Any]]:
    """Fetch word bounding boxes from DynamoDB."""
    try:
        client = _get_dynamo_client()
        words = client.list_receipt_words_from_receipt(image_id, receipt_id)
        return [
            {
                "text": w.text,
                "line_id": w.line_id,
                "word_id": w.word_id,
                "bbox": {
                    "x": w.bounding_box.get("x", 0),
                    "y": w.bounding_box.get("y", 0),
                    "width": w.bounding_box.get("width", 0),
                    "height": w.bounding_box.get("height", 0),
                },
            }
            for w in words
        ]
    except Exception as e:
        logger.warning("Failed to fetch words from DynamoDB for %s_%d: %s", image_id, receipt_id, e)
    return []


def _get_labels_from_dynamodb(image_id: str, receipt_id: int) -> dict[tuple[int, int], str]:
    """Fetch word labels from DynamoDB as a lookup dict."""
    try:
        client = _get_dynamo_client()
        labels, _ = client.list_receipt_word_labels_for_receipt(image_id, receipt_id)
        return {
            (label.line_id, label.word_id): label.label
            for label in labels
        }
    except Exception as e:
        logger.warning("Failed to fetch labels from DynamoDB for %s_%d: %s", image_id, receipt_id, e)
    return {}


def _build_receipt_visualization(trace_data: dict[str, Any]) -> dict[str, Any] | None:
    """Build visualization-ready receipt data from trace + DynamoDB data."""
    image_id = trace_data.get("image_id")
    receipt_id = trace_data.get("receipt_id")
    merchant_name = trace_data.get("merchant_name", "Unknown")
    execution_id = trace_data.get("execution_id", "")

    if not image_id or receipt_id is None:
        logger.warning("Missing image_id or receipt_id in trace data")
        return None

    # Get CDN info from DynamoDB
    receipt_info = _get_receipt_from_dynamodb(image_id, receipt_id)
    if not receipt_info:
        logger.warning("Could not fetch receipt CDN info from DynamoDB for %s_%d", image_id, receipt_id)
        return None

    # Get words from DynamoDB
    words = _get_words_from_dynamodb(image_id, receipt_id)
    if not words:
        logger.warning("No words found in DynamoDB for %s_%d", image_id, receipt_id)
        return None

    # Get labels from DynamoDB
    labels_lookup = _get_labels_from_dynamodb(image_id, receipt_id)

    # Attach labels to words
    viz_words = []
    for word in words:
        line_id = word["line_id"]
        word_id = word["word_id"]
        label = labels_lookup.get((line_id, word_id))
        viz_words.append({
            "text": word["text"],
            "label": label,
            "line_id": line_id,
            "word_id": word_id,
            "bbox": word["bbox"],
        })

    # Extract evaluation data from trace
    geometric = trace_data.get("geometric", {})
    currency = trace_data.get("currency", {})
    metadata = trace_data.get("metadata", {})
    financial = trace_data.get("financial", {})
    review = trace_data.get("review")
    line_item_duration = trace_data.get("line_item_duration_seconds")

    # Count total issues
    issues_found = (
        geometric.get("issues_found", 0)
        + currency.get("decisions", {}).get("INVALID", 0)
        + currency.get("decisions", {}).get("NEEDS_REVIEW", 0)
        + metadata.get("decisions", {}).get("INVALID", 0)
        + metadata.get("decisions", {}).get("NEEDS_REVIEW", 0)
        + financial.get("decisions", {}).get("INVALID", 0)
        + financial.get("decisions", {}).get("NEEDS_REVIEW", 0)
    )

    return {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "merchant_name": merchant_name,
        "issues_found": issues_found,
        "words": viz_words,
        "geometric": {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "issues_found": geometric.get("issues_found", 0),
            "issues": geometric.get("issues", []),
            "error": None,
            "merchant_receipts_analyzed": 0,
            "label_types_found": 0,
            "duration_seconds": geometric.get("duration_seconds", 0),
        },
        "currency": {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "merchant_name": merchant_name,
            "duration_seconds": currency.get("duration_seconds", 0),
            "decisions": currency.get("decisions", {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0}),
            "all_decisions": currency.get("all_decisions", []),
        },
        "metadata": {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "merchant_name": merchant_name,
            "duration_seconds": metadata.get("duration_seconds", 0),
            "decisions": metadata.get("decisions", {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0}),
            "all_decisions": metadata.get("all_decisions", []),
        },
        "financial": {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "merchant_name": merchant_name,
            "duration_seconds": financial.get("duration_seconds", 0),
            "decisions": financial.get("decisions", {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0}),
            "all_decisions": financial.get("all_decisions", []),
        },
        "review": {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "merchant_name": merchant_name,
            "duration_seconds": review.get("duration_seconds", 0),
            "decisions": review.get("decisions", {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0}),
            "all_decisions": review.get("all_decisions", []),
        } if review else None,
        "line_item_duration_seconds": line_item_duration,
        "cdn_s3_key": receipt_info["cdn_s3_key"],
        "cdn_webp_s3_key": receipt_info.get("cdn_webp_s3_key"),
        "cdn_avif_s3_key": receipt_info.get("cdn_avif_s3_key"),
        "cdn_medium_s3_key": receipt_info.get("cdn_medium_s3_key"),
        "cdn_medium_webp_s3_key": receipt_info.get("cdn_medium_webp_s3_key"),
        "cdn_medium_avif_s3_key": receipt_info.get("cdn_medium_avif_s3_key"),
        "width": receipt_info.get("width", 0),
        "height": receipt_info.get("height", 0),
    }


def _save_cache(cache_data: dict[str, Any]) -> bool:
    """Save visualization cache to S3."""
    try:
        s3_client.put_object(
            Bucket=S3_CACHE_BUCKET,
            Key=CACHE_KEY,
            Body=json.dumps(cache_data, default=str),
            ContentType="application/json",
        )
        logger.info("Saved visualization cache to s3://%s/%s", S3_CACHE_BUCKET, CACHE_KEY)
        return True
    except ClientError:
        logger.exception("Error saving cache")
        return False


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Generate Label Evaluator visualization cache from LangSmith exports.

    Reads receipt evaluations from LangSmith Parquet exports, enriches with
    DynamoDB data (words, labels, CDN keys), and outputs viz-sample-data.json.
    """
    logger.info("Starting Label Evaluator visualization cache generation")

    if not S3_CACHE_BUCKET or not LANGSMITH_EXPORT_BUCKET:
        return {"statusCode": 500, "error": "Missing environment variables"}

    # Get receipts with evaluation data from LangSmith
    logger.info(
        "Reading traces from s3://%s/%s",
        LANGSMITH_EXPORT_BUCKET,
        LANGSMITH_EXPORT_PREFIX,
    )
    trace_receipts = find_visualization_receipts_from_parquet(
        bucket=LANGSMITH_EXPORT_BUCKET,
        prefix=LANGSMITH_EXPORT_PREFIX,
        max_receipts=MAX_RECEIPTS * 3,  # Get more to allow for filtering
        batch_bucket=BATCH_BUCKET,  # Step Function results (currency, metadata, etc.)
    )

    if not trace_receipts:
        logger.warning("No receipts found in LangSmith export")
        return {"statusCode": 200, "message": "No receipts found", "receipts_cached": 0}

    logger.info("Found %d receipts in LangSmith export", len(trace_receipts))

    # Build visualization receipts by enriching with DynamoDB data
    all_receipts = []
    for trace_data in trace_receipts:
        viz_receipt = _build_receipt_visualization(trace_data)
        if viz_receipt:
            all_receipts.append(viz_receipt)
        if len(all_receipts) >= MAX_RECEIPTS * 2:
            break

    if not all_receipts:
        logger.warning("No receipts could be built from trace data")
        return {"statusCode": 200, "message": "No receipts built", "receipts_cached": 0}

    logger.info("Built %d visualization receipts", len(all_receipts))

    # Prioritize receipts with issues and select final set
    receipts_with_issues = [r for r in all_receipts if r["issues_found"] > 0]
    receipts_without_issues = [r for r in all_receipts if r["issues_found"] == 0]

    # Mix: mostly receipts with issues, some without
    if len(receipts_with_issues) >= MAX_RECEIPTS:
        selected = random.sample(receipts_with_issues, MAX_RECEIPTS)
    else:
        selected = receipts_with_issues.copy()
        remaining = MAX_RECEIPTS - len(selected)
        if receipts_without_issues and remaining > 0:
            selected.extend(random.sample(
                receipts_without_issues,
                min(remaining, len(receipts_without_issues))
            ))

    # Get execution_id from first receipt (all should be from same export)
    execution_id = trace_receipts[0].get("execution_id", "unknown") if trace_receipts else "unknown"

    # Build cache payload
    cache_data = {
        "execution_id": execution_id,
        "receipts": selected,
        "summary": {
            "total_receipts": len(selected),
            "receipts_with_issues": len([r for r in selected if r["issues_found"] > 0]),
        },
        "cached_at": datetime.now(timezone.utc).isoformat(),
    }

    # Save to S3
    if _save_cache(cache_data):
        logger.info("Successfully cached %d receipts", len(selected))
        return {
            "statusCode": 200,
            "message": "Cache generation complete",
            "receipts_cached": len(selected),
            "receipts_with_issues": cache_data["summary"]["receipts_with_issues"],
            "execution_id": execution_id,
        }
    else:
        return {"statusCode": 500, "error": "Failed to save cache"}
