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


def _extract_timestamp(execution_id: str) -> int:
    """Extract numeric timestamp from execution ID (e.g., 'viz-test-1767632575' -> 1767632575)."""
    try:
        # Get the last segment after splitting by '-'
        parts = execution_id.split("-")
        # Try to parse the last part as an integer timestamp
        return int(parts[-1])
    except (ValueError, IndexError):
        # If no valid timestamp, return 0 (will sort to end)
        return 0


def _list_executions() -> list[str]:
    """List recent execution IDs from the batch bucket, sorted by timestamp (newest first)."""
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
    # Sort by timestamp (newest first) instead of alphabetically
    return sorted(execution_ids, key=_extract_timestamp, reverse=True)


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


def _list_reviewed_files(execution_id: str) -> list[str]:
    """List all reviewed files for an execution."""
    keys = []
    try:
        paginator = s3_client.get_paginator("list_objects_v2")
        prefix = f"reviewed/{execution_id}/"
        for page in paginator.paginate(
            Bucket=LABEL_EVALUATOR_BATCH_BUCKET, Prefix=prefix
        ):
            for obj in page.get("Contents", []):
                key = obj.get("Key", "")
                if key.endswith(".json"):
                    keys.append(key)
    except ClientError:
        logger.debug("Error listing reviewed files for %s", execution_id)
    return keys


# Cache for reviewed files per execution to avoid repeated S3 listings
_reviewed_files_cache: dict[str, list[dict[str, Any]]] = {}


def _load_all_reviewed_data(execution_id: str) -> list[dict[str, Any]]:
    """Load and cache all reviewed data for an execution."""
    if execution_id in _reviewed_files_cache:
        return _reviewed_files_cache[execution_id]

    all_reviewed = []
    reviewed_keys = _list_reviewed_files(execution_id)

    for key in reviewed_keys:
        data = _load_json(LABEL_EVALUATOR_BATCH_BUCKET, key)
        if data:
            all_reviewed.append(data)

    _reviewed_files_cache[execution_id] = all_reviewed
    logger.debug("Loaded %d reviewed files for %s", len(all_reviewed), execution_id)
    return all_reviewed


def _load_evaluation_results(execution_id: str, image_id: str, receipt_id: int) -> dict[str, Any]:
    """Load evaluation results for a receipt from all evaluation folders.

    Returns dict with currency, metadata, financial, geometric, and review evaluations.
    """
    results = {
        "geometric": {"issues": [], "issues_found": 0, "error": None, "duration_seconds": 0},
        "currency": {"decisions": {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0}, "all_decisions": [], "duration_seconds": 0},
        "metadata": {"decisions": {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0}, "all_decisions": [], "duration_seconds": 0},
        "financial": {"decisions": {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0}, "all_decisions": [], "duration_seconds": 0},
        "review": {"decisions": {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0}, "all_decisions": [], "duration_seconds": 0},
    }

    # Load geometric results
    geometric_key = f"results/{execution_id}/{image_id}_{receipt_id}.json"
    geometric_data = _load_json(LABEL_EVALUATOR_BATCH_BUCKET, geometric_key)
    if geometric_data:
        issues = geometric_data.get("issues", [])
        results["geometric"]["issues"] = issues
        results["geometric"]["issues_found"] = len(issues)
        results["geometric"]["duration_seconds"] = geometric_data.get("compute_time_seconds", 0)

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

    # Load review results (from reviewed/ folder - per-merchant files)
    # Review only runs if geometric found issues, so filter by this receipt
    all_reviewed = _load_all_reviewed_data(execution_id)
    for reviewed_data in all_reviewed:
        # Get duration from this reviewed batch (applies to all issues in batch)
        batch_duration = reviewed_data.get("duration_seconds", 0)

        # Filter issues for this specific receipt
        for issue in reviewed_data.get("issues", []):
            if issue.get("image_id") == image_id and issue.get("receipt_id") == receipt_id:
                # This issue belongs to our receipt
                results["review"]["all_decisions"].append(issue)
                decision = issue.get("llm_review", {}).get("decision", "NEEDS_REVIEW")
                results["review"]["decisions"][decision] = results["review"]["decisions"].get(decision, 0) + 1
                # Use the batch duration (will be the same for all issues in this receipt)
                if batch_duration > 0:
                    results["review"]["duration_seconds"] = batch_duration

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

    # Skip receipts with data quality issues (all NEEDS_REVIEW = parsing failures)
    currency_decisions = evaluations["currency"]["decisions"]
    metadata_decisions = evaluations["metadata"]["decisions"]
    currency_total = sum(currency_decisions.values())
    metadata_total = sum(metadata_decisions.values())

    # If all currency or metadata decisions are NEEDS_REVIEW, skip this receipt
    if currency_total > 0 and currency_decisions.get("NEEDS_REVIEW", 0) == currency_total:
        logger.debug("Skipping %s_%d: all currency decisions are NEEDS_REVIEW (parsing failures)", image_id, receipt_id)
        return None
    if metadata_total > 0 and metadata_decisions.get("NEEDS_REVIEW", 0) == metadata_total:
        logger.debug("Skipping %s_%d: all metadata decisions are NEEDS_REVIEW (parsing failures)", image_id, receipt_id)
        return None

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
            "duration_seconds": evaluations["geometric"]["duration_seconds"],
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
        # Review runs after Geometric if issues were found - LLM decides V/I/R for flagged words
        "review": {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "merchant_name": merchant_name,
            "duration_seconds": evaluations["review"]["duration_seconds"],
            "decisions": evaluations["review"]["decisions"],
            "all_decisions": evaluations["review"]["all_decisions"],
        } if evaluations["review"]["all_decisions"] else None,
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


def _get_reviewed_receipt_ids(execution_id: str) -> set[tuple[str, int]]:
    """Get set of (image_id, receipt_id) tuples that have review data."""
    reviewed_ids = set()
    all_reviewed = _load_all_reviewed_data(execution_id)

    for reviewed_data in all_reviewed:
        for issue in reviewed_data.get("issues", []):
            image_id = issue.get("image_id")
            receipt_id = issue.get("receipt_id")
            if image_id and receipt_id is not None:
                reviewed_ids.add((image_id, receipt_id))

    logger.info("Found %d unique receipts with review data", len(reviewed_ids))
    return reviewed_ids


def _find_interesting_receipts(execution_id: str, max_count: int = 20) -> list[dict[str, Any]]:
    """Find receipts with interesting evaluation results.

    Prioritizes receipts in this order:
    1. Receipts with review data (highest priority for visualization demo)
    2. Receipts with geometric issues but no review data
    3. Receipts without issues
    """
    with_review = []
    with_issues = []
    without_issues = []

    # Get set of receipts that have review data for quick lookup
    reviewed_ids = _get_reviewed_receipt_ids(execution_id)
    receipt_files = _list_receipt_files(execution_id)

    # First pass: prioritize receipts that have review data
    for data_key in receipt_files:
        # Extract image_id and receipt_id from filename (e.g., "data/exec/uuid_1.json")
        filename = data_key.split("/")[-1].replace(".json", "")
        parts = filename.rsplit("_", 1)
        if len(parts) != 2:
            continue
        image_id, receipt_id_str = parts
        try:
            receipt_id = int(receipt_id_str)
        except ValueError:
            continue

        # Check if this receipt has review data
        has_review_data = (image_id, receipt_id) in reviewed_ids

        # Skip non-reviewed receipts in first pass (process them in second pass)
        if not has_review_data:
            continue

        receipt_data = _load_json(LABEL_EVALUATOR_BATCH_BUCKET, data_key)
        if not receipt_data:
            continue

        viz_receipt = _build_receipt_visualization(execution_id, receipt_data)
        if not viz_receipt:
            continue

        with_review.append(viz_receipt)
        if len(with_review) >= max_count:
            break

    logger.info("Found %d receipts with review data", len(with_review))

    # Second pass: fill remaining slots with receipts that have issues or none
    if len(with_review) < max_count:
        for data_key in receipt_files:
            if len(with_review) + len(with_issues) + len(without_issues) >= max_count * 2:
                break

            filename = data_key.split("/")[-1].replace(".json", "")
            parts = filename.rsplit("_", 1)
            if len(parts) != 2:
                continue
            image_id, receipt_id_str = parts
            try:
                receipt_id = int(receipt_id_str)
            except ValueError:
                continue

            # Skip already processed (reviewed) receipts
            if (image_id, receipt_id) in reviewed_ids:
                continue

            receipt_data = _load_json(LABEL_EVALUATOR_BATCH_BUCKET, data_key)
            if not receipt_data:
                continue

            viz_receipt = _build_receipt_visualization(execution_id, receipt_data)
            if not viz_receipt:
                continue

            if viz_receipt["issues_found"] > 0:
                with_issues.append(viz_receipt)
            else:
                without_issues.append(viz_receipt)

    # Combine in priority order
    return with_review + with_issues + without_issues


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

    logger.info("Found %d executions, using most recent", len(execution_ids))

    # Use only the most recent execution for consistency
    execution_id = execution_ids[0]
    all_receipts = _find_interesting_receipts(execution_id, max_count=MAX_RECEIPTS * 2)
    logger.info("Found %d receipts in %s", len(all_receipts), execution_id)

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
