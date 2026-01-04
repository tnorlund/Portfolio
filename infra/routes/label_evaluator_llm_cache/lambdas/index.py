"""Lambda handler for serving LLM evaluator cache.

This Lambda serves cached LLM evaluation examples for the
LLMEvaluatorVisualization component. It randomly selects
a receipt from the cache pool to provide variety.
"""

import json
import logging
import os
import random
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment variables
S3_CACHE_BUCKET = os.environ.get("S3_CACHE_BUCKET")
CACHE_PREFIX = "llm-evaluator-cache/receipts/"

if not S3_CACHE_BUCKET:
    logger.error("S3_CACHE_BUCKET environment variable not set")

# Initialize S3 client
s3_client = boto3.client("s3")


def _list_cached_receipts() -> list[str]:
    """List all cached receipt keys from S3."""
    keys = []
    try:
        paginator = s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=S3_CACHE_BUCKET, Prefix=CACHE_PREFIX):
            for obj in page.get("Contents", []):
                key = obj.get("Key", "")
                if key.endswith(".json"):
                    keys.append(key)
    except ClientError:
        logger.exception("Error listing cached receipts")
    return keys


def _fetch_receipt(key: str) -> dict[str, Any]:
    """Fetch a single receipt from S3."""
    response = s3_client.get_object(Bucket=S3_CACHE_BUCKET, Key=key)
    return json.loads(response["Body"].read().decode("utf-8"))


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Handle API Gateway requests for LLM evaluator cache.

    Returns a randomly selected receipt with LLM evaluation data.

    Response structure:
    {
        "receipt": {
            "image_id": str,
            "receipt_id": int,
            "merchant_name": str,
            "subtotal": float,
            "tax": float,
            "grand_total": float,
            "line_items": [{name: str, price: float}]
        },
        "evaluations": {
            "currency": [{
                "word_text": str,
                "current_label": str,
                "decision": "VALID" | "INVALID" | "NEEDS_REVIEW",
                "reasoning": str,
                "suggested_label": str | null,
                "confidence": "high" | "medium" | "low"
            }],
            "metadata": [{...}],
            "financial": {
                "subtotal": float,
                "tax": float,
                "expected_total": float,
                "actual_total": float,
                "difference": float,
                "decision": "VALID" | "INVALID" | "NEEDS_REVIEW",
                "reasoning": str,
                "wrong_value": "SUBTOTAL" | "TAX" | "GRAND_TOTAL" | null
            }
        },
        "pipeline": [{id: str, name: str, status: str}],
        "cached_at": str,
        "fetched_at": str
    }
    """
    logger.info("Received event: %s", event)

    # Handle API Gateway v2 event format
    try:
        http_method = event["requestContext"]["http"]["method"].upper()
    except (KeyError, TypeError) as e:
        logger.error("Invalid event structure: %s", e)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Invalid event structure"}),
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        }

    if http_method != "GET":
        return {
            "statusCode": 405,
            "body": json.dumps({"error": f"Method {http_method} not allowed"}),
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        }

    if not S3_CACHE_BUCKET:
        logger.error("S3_CACHE_BUCKET environment variable not set")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Configuration error: S3_CACHE_BUCKET not set"}),
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        }

    try:
        # List all cached receipts
        logger.info("Listing cached receipts from %s", CACHE_PREFIX)
        cached_keys = _list_cached_receipts()

        if not cached_keys:
            logger.warning("No receipts in cache pool")
            return {
                "statusCode": 404,
                "body": json.dumps({
                    "error": "Cache pool empty",
                    "message": "No cached receipts found. Run the cache generator first.",
                    "bucket": S3_CACHE_BUCKET,
                    "prefix": CACHE_PREFIX,
                }),
                "headers": {
                    "Content-Type": "application/json",
                    "Access-Control-Allow-Origin": "*",
                },
            }

        # Randomly select one receipt
        selected_key = random.choice(cached_keys)
        logger.info("Selected receipt: %s from pool of %d", selected_key, len(cached_keys))

        # Fetch the receipt
        receipt_data = _fetch_receipt(selected_key)

        # Add fetch timestamp
        receipt_data["fetched_at"] = datetime.now(timezone.utc).isoformat()
        receipt_data["pool_size"] = len(cached_keys)

        logger.info(
            "Returning receipt %s#%s",
            receipt_data.get("receipt", {}).get("image_id", "unknown"),
            receipt_data.get("receipt", {}).get("receipt_id", -1),
        )

        return {
            "statusCode": 200,
            "body": json.dumps(receipt_data, default=str),
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        }

    except ClientError as e:
        logger.error("S3 error: %s", e)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": f"S3 error: {str(e)}"}),
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        }
    except Exception as e:
        logger.error("Unexpected error: %s", e, exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
            },
        }
