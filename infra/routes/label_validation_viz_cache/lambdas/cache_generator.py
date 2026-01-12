"""Cache generator for Label Validation Visualization.

This Lambda generates visualization cache from LangSmith bulk exports,
combining trace data (validation decisions, timing) with DynamoDB data
(word bboxes, CDN keys).

Reads from the `receipt-label-validation` LangSmith project.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError
from receipt_dynamo import DynamoClient
from receipt_langsmith import (
    LabelValidationTraceIndex,
    ParquetReader,
    build_label_validation_summary,
    get_step_timings,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment variables
S3_CACHE_BUCKET = os.environ.get("S3_CACHE_BUCKET")
LANGSMITH_EXPORT_BUCKET = os.environ.get("LANGSMITH_EXPORT_BUCKET")
LANGSMITH_EXPORT_PREFIX = os.environ.get("LANGSMITH_EXPORT_PREFIX", "traces/")
DYNAMODB_TABLE_NAME = os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable")
RECEIPTS_PREFIX = "receipts/"
METADATA_KEY = "metadata.json"
MAX_RECEIPTS = 50  # Number of receipts to include in visualization cache

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


def _get_receipt_from_dynamodb(
    image_id: str, receipt_id: int
) -> dict[str, Any] | None:
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


def _get_words_from_dynamodb(
    image_id: str, receipt_id: int
) -> list[dict[str, Any]]:
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
        logger.warning(
            "Failed to fetch words from DynamoDB for %s_%d: %s",
            image_id,
            receipt_id,
            e,
        )
    return []


def _get_labels_from_dynamodb(
    image_id: str, receipt_id: int
) -> dict[tuple[int, int], dict[str, Any]]:
    """Fetch word labels from DynamoDB as a lookup dict.

    Returns dict mapping (line_id, word_id) -> label info dict with:
        - label: str
        - validation_status: str (PENDING, VALID, INVALID, NEEDS_REVIEW)
        - label_proposed_by: str (source of prediction)
    """
    try:
        client = _get_dynamo_client()
        labels, _ = client.list_receipt_word_labels_for_receipt(image_id, receipt_id)
        return {
            (label.line_id, label.word_id): {
                "label": label.label,
                "validation_status": label.validation_status or "PENDING",
                "label_proposed_by": label.label_proposed_by or "",
            }
            for label in labels
        }
    except Exception as e:
        logger.warning(
            "Failed to fetch labels from DynamoDB for %s_%d: %s",
            image_id,
            receipt_id,
            e,
        )
    return {}


def _parse_validation_traces(
    index: LabelValidationTraceIndex, root_id: str
) -> tuple[list[dict], list[dict]]:
    """Parse validation traces for a receipt.

    Returns:
        Tuple of (chroma_validations, llm_validations)
    """
    chroma_traces = []
    llm_traces = []

    validation_traces = index.get_validation_traces(root_id)
    for trace in validation_traces:
        name = trace.get("name", "")
        outputs = trace.get("outputs") or {}

        if name == "label_validation_chroma":
            chroma_traces.append(outputs)
        elif name == "label_validation_llm":
            llm_traces.append(outputs)

    return chroma_traces, llm_traces


def _build_validation_words(
    dynamo_words: list[dict],
    labels_lookup: dict[tuple[int, int], dict[str, Any]],
    chroma_validations: list[dict],
    llm_validations: list[dict],
) -> list[dict[str, Any]]:
    """Build word list with validation results.

    Matches trace validation outputs to DynamoDB words by line_id/word_id.
    Does NOT default missing trace validations to VALID - uses DynamoDB validation_status.

    Returns words with:
        - label: current label
        - validation_status: from DynamoDB (PENDING, VALID, INVALID, NEEDS_REVIEW)
        - validation_source: from traces (chroma, llm) or null if no trace
        - decision: from traces (VALID, INVALID, NEEDS_REVIEW) or null if no trace
    """
    # Build lookup from validation traces
    validation_lookup: dict[tuple[int, int], dict] = {}

    for v in chroma_validations:
        key = (v.get("line_id", 0), v.get("word_id", 0))
        decision = v.get("decision", "").upper()
        # Normalize CORRECT/CORRECTED -> INVALID
        if decision in ("CORRECT", "CORRECTED"):
            decision = "INVALID"
        validation_lookup[key] = {
            "validation_source": "chroma",
            "decision": decision if decision else None,
        }

    for v in llm_validations:
        key = (v.get("line_id", 0), v.get("word_id", 0))
        decision = v.get("decision", "").upper()
        # Normalize CORRECT/CORRECTED -> INVALID
        if decision in ("CORRECT", "CORRECTED"):
            decision = "INVALID"
        validation_lookup[key] = {
            "validation_source": "llm",
            "decision": decision if decision else None,
        }

    # Build visualization words
    viz_words = []
    for word in dynamo_words:
        line_id = word["line_id"]
        word_id = word["word_id"]
        key = (line_id, word_id)

        label_info = labels_lookup.get(key)
        validation = validation_lookup.get(key)

        # Only include words that have labels (validation targets)
        if label_info:
            viz_word = {
                "text": word["text"],
                "line_id": line_id,
                "word_id": word_id,
                "bbox": word["bbox"],
                "label": label_info["label"],
                # Include DynamoDB validation_status - this is the source of truth
                "validation_status": label_info["validation_status"],
                # Trace info - may be null if no trace found for this word
                "validation_source": validation.get("validation_source")
                if validation
                else None,
                "decision": validation.get("decision") if validation else None,
            }
            viz_words.append(viz_word)

    return viz_words


def _build_tier_summary(
    validations: list[dict], duration_seconds: float
) -> dict[str, Any]:
    """Build tier summary from validation traces.

    Does NOT default unknown decisions to VALID - counts them as UNKNOWN.
    """
    decisions = {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0, "UNKNOWN": 0}

    for v in validations:
        decision = v.get("decision", "").upper()
        # Normalize decision names
        if decision == "VALID":
            decisions["VALID"] += 1
        elif decision in ("INVALID", "CORRECTED", "CORRECT"):
            decisions["INVALID"] += 1
        elif decision in ("NEEDS_REVIEW", "NEEDS REVIEW"):
            decisions["NEEDS_REVIEW"] += 1
        elif decision:
            # Non-empty but unrecognized decision
            decisions["UNKNOWN"] += 1
        # Empty decision - don't count at all (trace output was incomplete)

    # Remove UNKNOWN key if zero for cleaner output
    if decisions["UNKNOWN"] == 0:
        del decisions["UNKNOWN"]

    return {
        "words_count": len(validations),
        "duration_seconds": duration_seconds,
        "decisions": decisions,
    }


def _build_receipt_visualization(
    root_trace: dict[str, Any],
    index: LabelValidationTraceIndex,
) -> dict[str, Any] | None:
    """Build visualization-ready receipt data from trace + DynamoDB data."""
    # Extract image/receipt IDs from trace metadata
    metadata = root_trace.get("extra", {}).get("metadata", {})
    image_id = metadata.get("image_id")
    receipt_id = metadata.get("receipt_id")

    if not image_id:
        # Try parsing from trace inputs
        inputs = root_trace.get("inputs") or {}
        image_id = inputs.get("image_id")
        receipt_id = inputs.get("receipt_id", 0)

    if not image_id or receipt_id is None:
        logger.warning("Missing image_id or receipt_id in trace")
        return None

    # Get receipt info from DynamoDB
    receipt_info = _get_receipt_from_dynamodb(image_id, receipt_id)
    if not receipt_info:
        logger.warning("Could not fetch receipt from DynamoDB: %s_%d", image_id, receipt_id)
        return None

    # Get words from DynamoDB
    dynamo_words = _get_words_from_dynamodb(image_id, receipt_id)
    if not dynamo_words:
        logger.warning("No words found for %s_%d", image_id, receipt_id)
        return None

    # Get labels from DynamoDB
    labels_lookup = _get_labels_from_dynamodb(image_id, receipt_id)

    # Parse validation traces
    root_id = root_trace.get("id", "")
    chroma_validations, llm_validations = _parse_validation_traces(index, root_id)

    # Get timing info
    children = index.get_children(root_id)
    timings = get_step_timings(root_trace, children)

    chroma_duration = timings.get("chroma_validation", {}).get("duration_ms", 0) / 1000
    llm_duration = timings.get("llm_validation", {}).get("duration_ms", 0) / 1000

    # Build visualization words
    viz_words = _build_validation_words(
        dynamo_words, labels_lookup, chroma_validations, llm_validations
    )

    if not viz_words:
        logger.warning("No validation words built for %s_%d", image_id, receipt_id)
        return None

    # Build tier summaries
    chroma_tier = _build_tier_summary(chroma_validations, chroma_duration)
    chroma_tier["tier"] = "chroma"

    llm_tier = None
    if llm_validations:
        llm_tier = _build_tier_summary(llm_validations, llm_duration)
        llm_tier["tier"] = "llm"

    # Get merchant name from outputs
    outputs = root_trace.get("outputs") or {}
    merchant_name = outputs.get("merchant_name")

    # Build step timings for UI (all upload lambda steps)
    step_timings = {}
    for step_name, step_data in timings.items():
        if isinstance(step_data, dict) and "duration_ms" in step_data:
            step_timings[step_name] = {
                "duration_ms": step_data["duration_ms"],
                "duration_seconds": step_data["duration_ms"] / 1000,
            }

    return {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "merchant_name": merchant_name,
        "words": viz_words,
        "chroma": chroma_tier,
        "llm": llm_tier,
        # Full step timing breakdown from get_step_timings
        "step_timings": step_timings,
        "cdn_s3_key": receipt_info["cdn_s3_key"],
        "cdn_webp_s3_key": receipt_info.get("cdn_webp_s3_key"),
        "cdn_avif_s3_key": receipt_info.get("cdn_avif_s3_key"),
        "cdn_medium_s3_key": receipt_info.get("cdn_medium_s3_key"),
        "cdn_medium_webp_s3_key": receipt_info.get("cdn_medium_webp_s3_key"),
        "cdn_medium_avif_s3_key": receipt_info.get("cdn_medium_avif_s3_key"),
        "width": receipt_info.get("width", 0),
        "height": receipt_info.get("height", 0),
    }


def _save_receipt(receipt: dict[str, Any]) -> str | None:
    """Save individual receipt to S3.

    Returns:
        S3 key if successful, None otherwise.
    """
    image_id = receipt["image_id"]
    receipt_id = receipt["receipt_id"]
    key = f"{RECEIPTS_PREFIX}receipt-{image_id}-{receipt_id}.json"

    try:
        s3_client.put_object(
            Bucket=S3_CACHE_BUCKET,
            Key=key,
            Body=json.dumps(receipt, default=str),
            ContentType="application/json",
        )
        return key
    except ClientError:
        logger.exception("Failed to save receipt %s", key)
        return None


def _save_metadata(receipt_keys: list[str], aggregate_stats: dict) -> bool:
    """Save metadata file to S3."""
    metadata = {
        "receipt_keys": receipt_keys,
        "total_receipts": len(receipt_keys),
        "aggregate_stats": aggregate_stats,
        "cached_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        s3_client.put_object(
            Bucket=S3_CACHE_BUCKET,
            Key=METADATA_KEY,
            Body=json.dumps(metadata, default=str),
            ContentType="application/json",
        )
        logger.info("Saved metadata.json with %d receipts", len(receipt_keys))
        return True
    except ClientError:
        logger.exception("Failed to save metadata")
        return False


def _calculate_aggregate_stats(receipts: list[dict]) -> dict[str, Any]:
    """Calculate aggregate statistics from receipts."""
    if not receipts:
        return {"total_receipts": 0, "avg_chroma_rate": 0.0}

    total_words = 0
    chroma_words = 0
    total_valid = 0
    total_invalid = 0
    total_needs_review = 0

    for r in receipts:
        chroma = r.get("chroma", {})
        llm = r.get("llm")

        chroma_count = chroma.get("words_count", 0)
        llm_count = llm.get("words_count", 0) if llm else 0

        total_words += chroma_count + llm_count
        chroma_words += chroma_count

        # Aggregate decisions
        chroma_decisions = chroma.get("decisions", {})
        total_valid += chroma_decisions.get("VALID", 0)
        total_invalid += chroma_decisions.get("INVALID", 0)
        total_needs_review += chroma_decisions.get("NEEDS_REVIEW", 0)

        if llm:
            llm_decisions = llm.get("decisions", {})
            total_valid += llm_decisions.get("VALID", 0)
            total_invalid += llm_decisions.get("INVALID", 0)
            total_needs_review += llm_decisions.get("NEEDS_REVIEW", 0)

    avg_chroma_rate = (chroma_words / total_words * 100) if total_words > 0 else 0.0

    return {
        "total_receipts": len(receipts),
        "avg_chroma_rate": round(avg_chroma_rate, 1),
        "total_valid": total_valid,
        "total_invalid": total_invalid,
        "total_needs_review": total_needs_review,
    }


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Generate Label Validation visualization cache from LangSmith exports.

    Reads receipt validation traces from LangSmith Parquet exports, enriches with
    DynamoDB data (words, labels, CDN keys), and outputs individual receipt JSON files.
    """
    logger.info("Starting Label Validation visualization cache generation")

    if not S3_CACHE_BUCKET or not LANGSMITH_EXPORT_BUCKET:
        return {"statusCode": 500, "error": "Missing environment variables"}

    # Read traces from LangSmith export
    logger.info(
        "Reading traces from s3://%s/%s",
        LANGSMITH_EXPORT_BUCKET,
        LANGSMITH_EXPORT_PREFIX,
    )

    try:
        reader = ParquetReader(bucket=LANGSMITH_EXPORT_BUCKET)
        traces = reader.read_all_traces(LANGSMITH_EXPORT_PREFIX)
    except Exception as e:
        logger.exception("Failed to read traces from Parquet")
        return {"statusCode": 500, "error": f"Failed to read traces: {e}"}

    if not traces:
        logger.warning("No traces found in LangSmith export")
        return {"statusCode": 200, "message": "No traces found", "receipts_cached": 0}

    logger.info("Read %d traces from Parquet", len(traces))

    # Build trace index
    index = LabelValidationTraceIndex(traces)
    root_runs = index.parents

    logger.info("Found %d root receipt_processing runs", len(root_runs))

    if not root_runs:
        return {"statusCode": 200, "message": "No root runs found", "receipts_cached": 0}

    # Build visualization receipts
    all_receipts = []
    for root in root_runs[:MAX_RECEIPTS * 2]:  # Get extra to allow for filtering
        viz_receipt = _build_receipt_visualization(root, index)
        if viz_receipt:
            all_receipts.append(viz_receipt)
        if len(all_receipts) >= MAX_RECEIPTS:
            break

    if not all_receipts:
        logger.warning("No receipts could be built from traces")
        return {"statusCode": 200, "message": "No receipts built", "receipts_cached": 0}

    logger.info("Built %d visualization receipts", len(all_receipts))

    # Save individual receipts to S3
    saved_keys = []
    for receipt in all_receipts:
        key = _save_receipt(receipt)
        if key:
            saved_keys.append(key)

    if not saved_keys:
        return {"statusCode": 500, "error": "Failed to save any receipts"}

    # Calculate aggregate stats and save metadata
    aggregate_stats = _calculate_aggregate_stats(all_receipts)
    if not _save_metadata(saved_keys, aggregate_stats):
        return {"statusCode": 500, "error": "Failed to save metadata"}

    logger.info("Successfully cached %d receipts", len(saved_keys))

    return {
        "statusCode": 200,
        "message": "Cache generation complete",
        "receipts_cached": len(saved_keys),
        "aggregate_stats": aggregate_stats,
    }
