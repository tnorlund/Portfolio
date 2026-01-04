"""Cache generator for LLM evaluator visualization.

This Lambda scans the label evaluator batch bucket for recent executions,
finds receipts with LLM-reviewed issues, and caches them in a
visualization-friendly format.
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
LABEL_EVALUATOR_BATCH_BUCKET = os.environ.get("LABEL_EVALUATOR_BATCH_BUCKET")
CACHE_PREFIX = "llm-evaluator-cache/receipts/"
MAX_CACHED_RECEIPTS = 50

if not S3_CACHE_BUCKET:
    logger.error("S3_CACHE_BUCKET environment variable not set")
if not LABEL_EVALUATOR_BATCH_BUCKET:
    logger.error("LABEL_EVALUATOR_BATCH_BUCKET environment variable not set")

s3_client = boto3.client("s3")

# Currency-related labels
CURRENCY_LABELS = {
    "LINE_TOTAL", "UNIT_PRICE", "SUBTOTAL", "TAX", "GRAND_TOTAL",
    "DISCOUNT", "TIP", "CHANGE", "CASH_TENDERED"
}

# Metadata-related labels
METADATA_LABELS = {
    "MERCHANT_NAME", "ADDRESS_LINE", "CITY_STATE_ZIP", "PHONE_NUMBER",
    "DATE", "TIME", "CARD_NUMBER", "STORE_NUMBER"
}


def _list_recent_executions() -> list[str]:
    """List recent execution IDs from the batch bucket."""
    execution_ids = set()
    try:
        paginator = s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(
            Bucket=LABEL_EVALUATOR_BATCH_BUCKET,
            Prefix="reviewed/",
            Delimiter="/",
        ):
            for prefix in page.get("CommonPrefixes", []):
                execution_id = prefix.get("Prefix", "").split("/")[1]
                if execution_id:
                    execution_ids.add(execution_id)
    except ClientError:
        logger.exception("Error listing executions")
    # Sort by name descending to get most recent first (names include timestamps)
    return sorted(execution_ids, reverse=True)


def _list_reviewed_files(execution_id: str) -> list[str]:
    """List reviewed result files for an execution."""
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
        logger.exception("Error listing reviewed files for %s", execution_id)
    return keys


def _load_json(key: str) -> dict[str, Any] | None:
    """Load JSON from the batch bucket."""
    try:
        response = s3_client.get_object(
            Bucket=LABEL_EVALUATOR_BATCH_BUCKET, Key=key
        )
        return json.loads(response["Body"].read().decode("utf-8"))
    except ClientError:
        logger.exception("Error loading %s", key)
        return None


def _find_interesting_receipts(
    execution_id: str, max_count: int = 10
) -> list[dict[str, Any]]:
    """Find receipts with interesting LLM reviews (INVALID decisions).

    Reviewed files are per-merchant-batch and contain issues from multiple receipts.
    Each issue has: image_id, receipt_id, issue (original), llm_review (LLM decision).
    """
    reviewed_files = _list_reviewed_files(execution_id)
    interesting_receipts = []
    seen_receipts = set()  # Track (image_id, receipt_id) to avoid duplicates

    for reviewed_key in reviewed_files:
        reviewed_data = _load_json(reviewed_key)
        if not reviewed_data:
            continue

        issues = reviewed_data.get("issues", [])
        if not issues:
            continue

        # Group issues by receipt
        receipts_in_file: dict[tuple, list] = {}
        for issue_entry in issues:
            image_id = issue_entry.get("image_id")
            receipt_id = issue_entry.get("receipt_id")
            if image_id is None or receipt_id is None:
                continue

            key = (image_id, receipt_id)
            if key not in receipts_in_file:
                receipts_in_file[key] = []
            receipts_in_file[key].append(issue_entry)

        # Check each receipt for interesting issues
        for (image_id, receipt_id), receipt_issues in receipts_in_file.items():
            if (image_id, receipt_id) in seen_receipts:
                continue

            # Look for INVALID decisions
            has_invalid = any(
                entry.get("llm_review", {}).get("decision") == "INVALID"
                for entry in receipt_issues
            )

            if has_invalid or len(receipt_issues) >= 2:
                seen_receipts.add((image_id, receipt_id))
                interesting_receipts.append({
                    "execution_id": execution_id,
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                    "reviewed_key": reviewed_key,
                    "issues": receipt_issues,
                    "has_invalid": has_invalid,
                })

        if len(interesting_receipts) >= max_count:
            break

    return interesting_receipts


def _categorize_evaluations(
    issues: list[dict[str, Any]]
) -> tuple[list[dict], list[dict]]:
    """Categorize issues into currency and metadata evaluations.

    Each issue entry has: issue (original issue dict), llm_review (LLM decision dict).
    """
    currency_evals = []
    metadata_evals = []

    for issue_entry in issues:
        # Handle new structure: {issue: {...}, llm_review: {...}}
        original_issue = issue_entry.get("issue", issue_entry)
        llm_review = issue_entry.get("llm_review", {})
        if not llm_review:
            continue

        current_label = original_issue.get("current_label") or ""
        suggested_label = original_issue.get("suggested_label") or llm_review.get("suggested_label")

        eval_item = {
            "word_text": original_issue.get("word_text", ""),
            "current_label": current_label,
            "decision": llm_review.get("decision", "NEEDS_REVIEW"),
            "reasoning": llm_review.get("reasoning", original_issue.get("reasoning", "")),
            "suggested_label": suggested_label,
            "confidence": llm_review.get("confidence", "medium"),
        }

        # Categorize by label type
        if current_label in CURRENCY_LABELS or (suggested_label and suggested_label in CURRENCY_LABELS):
            currency_evals.append(eval_item)
        elif current_label in METADATA_LABELS or (suggested_label and suggested_label in METADATA_LABELS):
            metadata_evals.append(eval_item)
        else:
            # Default to metadata if unclear
            metadata_evals.append(eval_item)

    return currency_evals, metadata_evals


def _extract_financial_math(receipt_data: dict[str, Any]) -> dict[str, Any]:
    """Extract financial math validation from receipt data."""
    # Extract labeled values
    words = receipt_data.get("words", [])
    labels = receipt_data.get("labels", [])

    # Build label lookup
    labels_by_word = {}
    for label in labels:
        key = (label.get("line_id"), label.get("word_id"))
        labels_by_word[key] = label

    # Find financial values
    subtotal = None
    tax = None
    grand_total = None

    for word in words:
        key = (word.get("line_id"), word.get("word_id"))
        label_info = labels_by_word.get(key, {})
        label = label_info.get("label")
        text = word.get("text", "")

        # Try to parse currency value
        try:
            value = float(text.replace("$", "").replace(",", ""))
        except ValueError:
            continue

        if label == "SUBTOTAL":
            subtotal = value
        elif label == "TAX":
            tax = value
        elif label == "GRAND_TOTAL":
            grand_total = value

    # Calculate expected vs actual
    if subtotal is not None and tax is not None:
        expected_total = round(subtotal + tax, 2)
    else:
        expected_total = grand_total or 0

    actual_total = grand_total or 0
    difference = round(abs(expected_total - actual_total), 2) if expected_total else 0

    # Determine decision
    if difference == 0:
        decision = "VALID"
        reasoning = f"Financial math verified: ${subtotal or 0:.2f} + ${tax or 0:.2f} = ${actual_total:.2f}"
        wrong_value = None
    elif difference < 0.10:
        decision = "VALID"
        reasoning = f"Minor rounding difference of ${difference:.2f} is acceptable."
        wrong_value = None
    else:
        decision = "INVALID"
        reasoning = f"Math mismatch: ${subtotal or 0:.2f} + ${tax or 0:.2f} = ${expected_total:.2f}, but GRAND_TOTAL shows ${actual_total:.2f}. Difference of ${difference:.2f}."
        wrong_value = "GRAND_TOTAL"

    return {
        "equation": "GRAND_TOTAL = SUBTOTAL + TAX",
        "subtotal": subtotal or 0,
        "tax": tax or 0,
        "expected_total": expected_total,
        "actual_total": actual_total,
        "difference": difference,
        "decision": decision,
        "reasoning": reasoning,
        "wrong_value": wrong_value,
    }


def _build_visualization_data(
    execution_id: str,
    image_id: str,
    receipt_id: int,
    reviewed_data: dict[str, Any],
) -> dict[str, Any] | None:
    """Build visualization-ready data for a receipt."""
    # Load receipt data
    data_key = f"data/{execution_id}/{image_id}_{receipt_id}.json"
    receipt_data = _load_json(data_key)
    if not receipt_data:
        logger.warning("Could not load receipt data: %s", data_key)
        return None

    place = receipt_data.get("place", {})
    merchant_name = place.get("merchant_name", "Unknown") if place else "Unknown"

    # Categorize evaluations
    issues = reviewed_data.get("issues", [])
    currency_evals, metadata_evals = _categorize_evaluations(issues)

    # Extract financial math
    financial = _extract_financial_math(receipt_data)

    # Build pipeline stages
    pipeline = [
        {"id": "input", "name": "Load Receipt", "status": "complete"},
        {"id": "currency", "name": "Currency Review", "status": "complete"},
        {"id": "metadata", "name": "Metadata Review", "status": "complete"},
        {"id": "financial", "name": "Financial Math", "status": "complete"},
        {"id": "output", "name": "Apply Decisions", "status": "complete"},
    ]

    return {
        "receipt": {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "merchant_name": merchant_name,
            "subtotal": financial["subtotal"],
            "tax": financial["tax"],
            "grand_total": financial["actual_total"],
            "line_items": [],  # Would need to extract from receipt
        },
        "evaluations": {
            "currency": currency_evals,
            "metadata": metadata_evals,
            "financial": financial,
        },
        "pipeline": pipeline,
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "execution_id": execution_id,
    }


def _save_to_cache(receipt_data: dict[str, Any]) -> bool:
    """Save receipt data to the cache bucket."""
    image_id = receipt_data.get("receipt", {}).get("image_id", "unknown")
    receipt_id = receipt_data.get("receipt", {}).get("receipt_id", 0)
    key = f"{CACHE_PREFIX}receipt-{image_id}-{receipt_id}.json"

    try:
        s3_client.put_object(
            Bucket=S3_CACHE_BUCKET,
            Key=key,
            Body=json.dumps(receipt_data, default=str),
            ContentType="application/json",
        )
        logger.info("Cached receipt to %s", key)
        return True
    except ClientError:
        logger.exception("Error saving to cache: %s", key)
        return False


def _cleanup_old_cache() -> int:
    """Remove old cache entries if over limit."""
    try:
        response = s3_client.list_objects_v2(
            Bucket=S3_CACHE_BUCKET, Prefix=CACHE_PREFIX
        )
        objects = response.get("Contents", [])

        if len(objects) <= MAX_CACHED_RECEIPTS:
            return 0

        objects.sort(key=lambda x: x.get("LastModified", datetime.min))
        to_delete = objects[: len(objects) - MAX_CACHED_RECEIPTS]

        for obj in to_delete:
            s3_client.delete_object(Bucket=S3_CACHE_BUCKET, Key=obj["Key"])

        logger.info("Cleaned up %d old cache entries", len(to_delete))
        return len(to_delete)
    except ClientError:
        logger.exception("Error cleaning up cache")
        return 0


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Generate LLM evaluator cache."""
    logger.info("Starting LLM evaluator cache generation")

    if not S3_CACHE_BUCKET or not LABEL_EVALUATOR_BATCH_BUCKET:
        return {"statusCode": 500, "error": "Missing environment variables"}

    execution_ids = _list_recent_executions()
    if not execution_ids:
        logger.warning("No executions found")
        return {"statusCode": 200, "message": "No executions found", "cached_count": 0}

    logger.info("Found %d executions", len(execution_ids))

    all_interesting = []
    for execution_id in execution_ids[:3]:
        interesting = _find_interesting_receipts(execution_id, max_count=20)
        all_interesting.extend(interesting)
        logger.info("Found %d interesting receipts in %s", len(interesting), execution_id)

    if not all_interesting:
        logger.warning("No interesting LLM reviews found")
        return {"statusCode": 200, "message": "No LLM reviews found", "cached_count": 0}

    if len(all_interesting) > MAX_CACHED_RECEIPTS:
        all_interesting = random.sample(all_interesting, MAX_CACHED_RECEIPTS)

    cached_count = 0
    for receipt_info in all_interesting:
        reviewed_data = _load_json(receipt_info["reviewed_key"])
        if not reviewed_data:
            continue

        viz_data = _build_visualization_data(
            execution_id=receipt_info["execution_id"],
            image_id=receipt_info["image_id"],
            receipt_id=receipt_info["receipt_id"],
            reviewed_data=reviewed_data,
        )

        if viz_data and _save_to_cache(viz_data):
            cached_count += 1

    cleaned_count = _cleanup_old_cache()

    logger.info("Cache complete: %d cached, %d cleaned", cached_count, cleaned_count)

    return {
        "statusCode": 200,
        "message": "Cache generation complete",
        "executions_scanned": len(execution_ids[:3]),
        "interesting_found": len(all_interesting),
        "cached_count": cached_count,
        "cleaned_count": cleaned_count,
    }
