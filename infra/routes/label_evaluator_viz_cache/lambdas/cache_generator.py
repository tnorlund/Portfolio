"""Cache generator for Label Evaluator Visualization.

This Lambda generates viz-sample-data.json from recent label evaluator executions,
combining receipt data with evaluation results and line item pattern durations.
"""

import hashlib
import json
import logging
import os
import random
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError
from receipt_dynamo import DynamoClient

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment variables
S3_CACHE_BUCKET = os.environ.get("S3_CACHE_BUCKET")
LABEL_EVALUATOR_BATCH_BUCKET = os.environ.get("LABEL_EVALUATOR_BATCH_BUCKET")
DYNAMODB_TABLE_NAME = os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable")
CACHE_KEY = "viz-sample-data.json"
MAX_RECEIPTS = 10  # Number of receipts to include in visualization cache

if not S3_CACHE_BUCKET:
    logger.error("S3_CACHE_BUCKET environment variable not set")
if not LABEL_EVALUATOR_BATCH_BUCKET:
    logger.error("LABEL_EVALUATOR_BATCH_BUCKET environment variable not set")

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


def _get_merchant_hash(merchant_name: str) -> str:
    """Get hash of merchant name for pattern file lookup.

    Uses 12-character hash with original case to match the pattern file naming convention.
    """
    return hashlib.sha256(merchant_name.encode()).hexdigest()[:12]


def _load_json(bucket: str, key: str) -> dict[str, Any] | None:
    """Load JSON from S3."""
    try:
        response = s3_client.get_object(Bucket=bucket, Key=key)
        return json.loads(response["Body"].read().decode("utf-8"))
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "NoSuchKey":
            logger.debug("Key not found: s3://%s/%s", bucket, key)
        else:
            logger.warning("Error loading s3://%s/%s: %s", bucket, key, e)
        return None


def _list_executions() -> list[str]:
    """List recent execution IDs from the batch bucket."""
    execution_ids = set()
    try:
        paginator = s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(
            Bucket=LABEL_EVALUATOR_BATCH_BUCKET,
            Prefix="data/",
            Delimiter="/",
        ):
            for prefix in page.get("CommonPrefixes", []):
                execution_id = prefix.get("Prefix", "").split("/")[1]
                if execution_id:
                    execution_ids.add(execution_id)
    except ClientError:
        logger.exception("Error listing executions")
    return sorted(execution_ids, reverse=True)


def _list_receipt_files(execution_id: str) -> list[str]:
    """List receipt data files for an execution."""
    keys = []
    try:
        paginator = s3_client.get_paginator("list_objects_v2")
        prefix = f"data/{execution_id}/"
        for page in paginator.paginate(
            Bucket=LABEL_EVALUATOR_BATCH_BUCKET, Prefix=prefix
        ):
            for obj in page.get("Contents", []):
                key = obj.get("Key", "")
                if key.endswith(".json") and "_" in key.split("/")[-1]:
                    keys.append(key)
    except ClientError:
        logger.exception("Error listing receipt files for %s", execution_id)
    return keys


def _get_line_item_duration(merchant_name: str) -> float | None:
    """Get line item structure discovery duration for a merchant.

    Looks up the merchant's pattern file and extracts
    _trace_metadata.discovery_duration_seconds.
    """
    merchant_hash = _get_merchant_hash(merchant_name)
    pattern_key = f"line_item_patterns/{merchant_hash}.json"

    pattern_data = _load_json(LABEL_EVALUATOR_BATCH_BUCKET, pattern_key)
    if not pattern_data:
        logger.debug("No pattern file for merchant: %s (%s)", merchant_name, merchant_hash)
        return None

    trace_metadata = pattern_data.get("_trace_metadata", {})
    duration = trace_metadata.get("discovery_duration_seconds")

    if duration is not None:
        logger.debug("Line item duration for %s: %.3fs", merchant_name, duration)

    return duration


def _load_evaluation_results(execution_id: str, image_id: str, receipt_id: int) -> dict[str, Any]:
    """Load evaluation results for a receipt from all evaluation folders.

    Returns dict with currency, metadata, financial, and geometric evaluations.
    """
    results = {
        "geometric": {"issues": [], "issues_found": 0, "error": None},
        "currency": {"decisions": {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0}, "all_decisions": [], "duration_seconds": 0},
        "metadata": {"decisions": {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0}, "all_decisions": [], "duration_seconds": 0},
        "financial": {"decisions": {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0}, "all_decisions": [], "duration_seconds": 0},
    }

    # Load geometric results
    geometric_key = f"results/{execution_id}/{image_id}_{receipt_id}.json"
    geometric_data = _load_json(LABEL_EVALUATOR_BATCH_BUCKET, geometric_key)
    if geometric_data:
        issues = geometric_data.get("issues", [])
        results["geometric"]["issues"] = issues
        results["geometric"]["issues_found"] = len(issues)

    # Load currency results
    currency_key = f"currency/{execution_id}/{image_id}_{receipt_id}.json"
    currency_data = _load_json(LABEL_EVALUATOR_BATCH_BUCKET, currency_key)
    if currency_data:
        all_decisions = currency_data.get("all_decisions", [])
        results["currency"]["all_decisions"] = all_decisions
        results["currency"]["duration_seconds"] = currency_data.get("duration_seconds", 0)
        for d in all_decisions:
            decision = d.get("llm_review", {}).get("decision", "NEEDS_REVIEW")
            results["currency"]["decisions"][decision] = results["currency"]["decisions"].get(decision, 0) + 1

    # Load metadata results
    metadata_key = f"metadata/{execution_id}/{image_id}_{receipt_id}.json"
    metadata_data = _load_json(LABEL_EVALUATOR_BATCH_BUCKET, metadata_key)
    if metadata_data:
        all_decisions = metadata_data.get("all_decisions", [])
        results["metadata"]["all_decisions"] = all_decisions
        results["metadata"]["duration_seconds"] = metadata_data.get("duration_seconds", 0)
        for d in all_decisions:
            decision = d.get("llm_review", {}).get("decision", "NEEDS_REVIEW")
            results["metadata"]["decisions"][decision] = results["metadata"]["decisions"].get(decision, 0) + 1

    # Load financial results
    financial_key = f"financial/{execution_id}/{image_id}_{receipt_id}.json"
    financial_data = _load_json(LABEL_EVALUATOR_BATCH_BUCKET, financial_key)
    if financial_data:
        all_decisions = financial_data.get("all_decisions", [])
        results["financial"]["all_decisions"] = all_decisions
        results["financial"]["duration_seconds"] = financial_data.get("duration_seconds", 0)
        for d in all_decisions:
            decision = d.get("llm_review", {}).get("decision", "NEEDS_REVIEW")
            results["financial"]["decisions"][decision] = results["financial"]["decisions"].get(decision, 0) + 1

    return results


def _build_receipt_visualization(
    execution_id: str,
    receipt_data: dict[str, Any],
) -> dict[str, Any] | None:
    """Build visualization-ready receipt data."""
    # Extract basic receipt info - image_id and receipt_id are at top level
    image_id = receipt_data.get("image_id")
    receipt_id = receipt_data.get("receipt_id")
    merchant_name = receipt_data.get("merchant_name", "Unknown")
    place = receipt_data.get("place", {})
    words = receipt_data.get("words", [])
    labels = receipt_data.get("labels", [])

    # Fallback merchant name from place if not at top level
    if merchant_name == "Unknown" and place:
        merchant_name = place.get("merchant_name", "Unknown")

    if not image_id or receipt_id is None:
        logger.warning("Missing image_id or receipt_id in receipt data")
        return None

    # Get line item discovery duration
    line_item_duration = _get_line_item_duration(merchant_name)

    # Load evaluation results
    evaluations = _load_evaluation_results(execution_id, image_id, receipt_id)

    # Build label lookup
    labels_by_word = {}
    for label in labels:
        key = (label.get("line_id"), label.get("word_id"))
        labels_by_word[key] = label

    # Build words with labels and bounding boxes
    viz_words = []
    for word in words:
        line_id = word.get("line_id")
        word_id = word.get("word_id")
        label_info = labels_by_word.get((line_id, word_id), {})

        bbox = word.get("bounding_box", {})
        viz_words.append({
            "text": word.get("text", ""),
            "label": label_info.get("label"),
            "line_id": line_id,
            "word_id": word_id,
            "bbox": {
                "x": bbox.get("x", 0),
                "y": bbox.get("y", 0),
                "width": bbox.get("width", 0),
                "height": bbox.get("height", 0),
            },
        })

    # Count total issues
    issues_found = (
        evaluations["geometric"]["issues_found"]
        + evaluations["currency"]["decisions"]["INVALID"]
        + evaluations["currency"]["decisions"]["NEEDS_REVIEW"]
        + evaluations["metadata"]["decisions"]["INVALID"]
        + evaluations["metadata"]["decisions"]["NEEDS_REVIEW"]
        + evaluations["financial"]["decisions"]["INVALID"]
        + evaluations["financial"]["decisions"]["NEEDS_REVIEW"]
    )

    # Get CDN image keys from DynamoDB
    receipt_info = _get_receipt_from_dynamodb(image_id, receipt_id)
    if not receipt_info:
        logger.warning("Could not fetch receipt CDN info from DynamoDB for %s_%d", image_id, receipt_id)
        return None

    return {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "merchant_name": merchant_name,
        "issues_found": issues_found,
        "words": viz_words,
        "geometric": {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "issues_found": evaluations["geometric"]["issues_found"],
            "issues": evaluations["geometric"]["issues"],
            "error": evaluations["geometric"]["error"],
            "merchant_receipts_analyzed": 0,
            "label_types_found": 0,
        },
        "currency": {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "merchant_name": merchant_name,
            "duration_seconds": evaluations["currency"]["duration_seconds"],
            "decisions": evaluations["currency"]["decisions"],
            "all_decisions": evaluations["currency"]["all_decisions"],
        },
        "metadata": {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "merchant_name": merchant_name,
            "duration_seconds": evaluations["metadata"]["duration_seconds"],
            "decisions": evaluations["metadata"]["decisions"],
            "all_decisions": evaluations["metadata"]["all_decisions"],
        },
        "financial": {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "merchant_name": merchant_name,
            "duration_seconds": evaluations["financial"]["duration_seconds"],
            "decisions": evaluations["financial"]["decisions"],
            "all_decisions": evaluations["financial"]["all_decisions"],
        },
        # Line item structure duration from pattern file
        "line_item_duration_seconds": line_item_duration,
        # CDN image keys from DynamoDB
        "cdn_s3_key": receipt_info["cdn_s3_key"],
        "cdn_webp_s3_key": receipt_info.get("cdn_webp_s3_key"),
        "cdn_avif_s3_key": receipt_info.get("cdn_avif_s3_key"),
        "cdn_medium_s3_key": receipt_info.get("cdn_medium_s3_key"),
        "cdn_medium_webp_s3_key": receipt_info.get("cdn_medium_webp_s3_key"),
        "cdn_medium_avif_s3_key": receipt_info.get("cdn_medium_avif_s3_key"),
        "width": receipt_info.get("width", 0),
        "height": receipt_info.get("height", 0),
    }


def _find_interesting_receipts(execution_id: str, max_count: int = 20) -> list[dict[str, Any]]:
    """Find receipts with interesting evaluation results."""
    interesting = []
    receipt_files = _list_receipt_files(execution_id)

    for data_key in receipt_files:
        if len(interesting) >= max_count:
            break

        receipt_data = _load_json(LABEL_EVALUATOR_BATCH_BUCKET, data_key)
        if not receipt_data:
            continue

        viz_receipt = _build_receipt_visualization(execution_id, receipt_data)
        if not viz_receipt:
            continue

        # Prioritize receipts with issues
        if viz_receipt["issues_found"] > 0:
            interesting.insert(0, viz_receipt)
        else:
            interesting.append(viz_receipt)

    return interesting


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
    """Generate Label Evaluator visualization cache.

    Scans recent executions for receipts with evaluations and builds
    viz-sample-data.json for the visualization component.
    """
    logger.info("Starting Label Evaluator visualization cache generation")

    if not S3_CACHE_BUCKET or not LABEL_EVALUATOR_BATCH_BUCKET:
        return {"statusCode": 500, "error": "Missing environment variables"}

    # Find recent executions
    execution_ids = _list_executions()
    if not execution_ids:
        logger.warning("No executions found")
        return {"statusCode": 200, "message": "No executions found", "receipts_cached": 0}

    logger.info("Found %d executions, checking most recent", len(execution_ids))

    # Gather receipts from recent executions
    all_receipts = []
    for execution_id in execution_ids[:3]:  # Check up to 3 most recent
        receipts = _find_interesting_receipts(execution_id, max_count=MAX_RECEIPTS * 2)
        logger.info("Found %d receipts in %s", len(receipts), execution_id)
        all_receipts.extend(receipts)

        if len(all_receipts) >= MAX_RECEIPTS * 3:
            break

    if not all_receipts:
        logger.warning("No receipts found with evaluation data")
        return {"statusCode": 200, "message": "No receipts found", "receipts_cached": 0}

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

    # Build cache payload
    cache_data = {
        "execution_id": execution_ids[0],
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
            "execution_id": execution_ids[0],
        }
    else:
        return {"statusCode": 500, "error": "Failed to save cache"}
