#!/usr/bin/env python3
"""EMR Serverless job entry point for visualization cache generation.

This script generates versioned cache files from:
1. LangSmith Parquet exports (trace data with words/labels)
2. Receipt lookup JSON from S3 (CDN keys from DynamoDB, pre-exported by Lambda)
3. Step Function results in batch bucket (currency, metadata, financial, geometric)

Output:
- viz-cache-{timestamp}.json  - Versioned cache file
- latest.json                 - Pointer to current cache version

Usage:
    spark-submit \\
        --conf spark.executor.memory=4g \\
        --conf spark.executor.cores=2 \\
        --conf spark.sql.legacy.parquet.nanosAsLong=true \\
        viz_cache_job.py \\
        --parquet-bucket langsmith-export-bucket \\
        --batch-bucket label-evaluator-batch-bucket \\
        --cache-bucket viz-cache-bucket \\
        --receipts-json s3://cache-bucket/receipts-lookup.json

Note: If --parquet-prefix is not provided, the latest export is auto-detected.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

import boto3
from pyspark.sql import SparkSession
from receipt_langsmith.entities.visualization import (
    BoundingBox,
    DecisionCounts,
    EvaluatorResult,
    GeometricResult,
    VizCacheReceipt,
    WordWithLabel,
)
from receipt_langsmith.parsers.trace_helpers import load_s3_result
from receipt_langsmith.spark.processor import LangSmithSparkProcessor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# --- Argument Parsing ---


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Visualization cache generator for EMR Serverless"
    )

    parser.add_argument(
        "--parquet-bucket",
        required=True,
        help="S3 bucket containing LangSmith Parquet exports",
    )
    parser.add_argument(
        "--parquet-prefix",
        default="traces/",
        help="S3 prefix for Parquet files (default: traces/)",
    )
    parser.add_argument(
        "--batch-bucket",
        required=True,
        help="S3 bucket with Step Function results (currency, metadata, etc.)",
    )
    parser.add_argument(
        "--cache-bucket",
        required=True,
        help="S3 bucket to write viz-sample-data.json",
    )
    parser.add_argument(
        "--receipts-json",
        required=True,
        help="S3 path to receipts-lookup.json",
    )
    parser.add_argument(
        "--execution-id",
        default=None,
        help="Execution ID to filter batch bucket results (optional)",
    )

    return parser.parse_args()


# --- S3 Utilities ---


def load_receipts_from_s3(
    s3_client: Any, receipts_json_path: str
) -> dict[tuple[str, int], dict[str, Any]]:
    """Load receipt lookup from S3 JSON file.

    Returns:
        dict mapping (image_id, receipt_id) -> receipt data dict containing:
            - cdn_s3_key, cdn_webp_s3_key, cdn_avif_s3_key
            - cdn_medium_s3_key, cdn_medium_webp_s3_key, cdn_medium_avif_s3_key
            - width, height
    """
    logger.info("Loading receipts from %s", receipts_json_path)

    if not receipts_json_path.startswith("s3://"):
        raise ValueError(f"Invalid S3 path: {receipts_json_path}")

    path = receipts_json_path[5:]
    if "/" not in path:
        raise ValueError(f"Invalid S3 path format: {receipts_json_path}")

    bucket, key = path.split("/", 1)
    response = s3_client.get_object(Bucket=bucket, Key=key)
    raw_lookup = json.loads(response["Body"].read().decode("utf-8"))

    # Convert "{image_id}_{receipt_id}" -> (image_id, receipt_id)
    lookup: dict[tuple[str, int], dict[str, Any]] = {}
    for composite_key, receipt_data in raw_lookup.items():
        parts = composite_key.rsplit("_", 1)
        if len(parts) == 2:
            image_id, receipt_id_str = parts
            try:
                # Handle both old format (string) and new format (dict)
                if isinstance(receipt_data, str):
                    # Legacy format: just cdn_s3_key
                    lookup[(image_id, int(receipt_id_str))] = {
                        "cdn_s3_key": receipt_data,
                        "cdn_webp_s3_key": None,
                        "cdn_medium_s3_key": None,
                        "width": 800,
                        "height": 2400,
                    }
                else:
                    lookup[(image_id, int(receipt_id_str))] = receipt_data
            except ValueError:
                continue

    logger.info("Loaded %d receipts from S3", len(lookup))
    return lookup


def find_latest_export_prefix(
    s3_client: Any, bucket: str, preferred_export_id: str | None = None
) -> str | None:
    """Find the latest LangSmith export prefix in the bucket."""
    logger.info("Finding latest export in s3://%s/traces/", bucket)

    try:
        response = s3_client.list_objects_v2(
            Bucket=bucket, Prefix="traces/", Delimiter="/"
        )
        prefixes = response.get("CommonPrefixes", [])
        if not prefixes:
            logger.warning("No export folders found in traces/")
            return None

        export_ids = _extract_export_ids(prefixes)
        if not export_ids:
            logger.warning("No valid export IDs found")
            return None

        logger.info("Found %d exports: %s", len(export_ids), export_ids[:5])

        # Check preferred export first
        if preferred_export_id and preferred_export_id in export_ids:
            check_prefix = f"traces/export_id={preferred_export_id}/"
            if _prefix_has_data(s3_client, bucket, check_prefix):
                logger.info("Using preferred export: %s", preferred_export_id)
                return check_prefix
            logger.warning(
                "Preferred export %s has no data", preferred_export_id
            )

        # Find most recent export
        return _find_most_recent_export(s3_client, bucket, export_ids)

    except Exception:
        logger.exception("Failed to find latest export")
        return None


def _extract_export_ids(prefixes: list[dict[str, Any]]) -> list[str]:
    """Extract export IDs from S3 prefix list."""
    export_ids = []
    for p in prefixes:
        prefix = p["Prefix"]
        if "export_id=" in prefix:
            export_id = prefix.split("export_id=")[1].rstrip("/")
            if export_id:
                export_ids.append(export_id)
    return export_ids


def _prefix_has_data(s3_client: Any, bucket: str, prefix: str) -> bool:
    """Check if an S3 prefix has any objects."""
    response = s3_client.list_objects_v2(
        Bucket=bucket, Prefix=prefix, MaxKeys=1
    )
    return bool(response.get("Contents"))


def _find_most_recent_export(
    s3_client: Any, bucket: str, export_ids: list[str]
) -> str | None:
    """Find the export with the most recent modification time."""
    latest_export = None
    latest_time = None

    for export_id in export_ids:
        check_prefix = f"traces/export_id={export_id}/"
        response = s3_client.list_objects_v2(
            Bucket=bucket, Prefix=check_prefix, MaxKeys=1
        )
        if response.get("Contents"):
            mod_time = response["Contents"][0].get("LastModified")
            if latest_time is None or mod_time > latest_time:
                latest_time = mod_time
                latest_export = export_id

    if latest_export:
        prefix = f"traces/export_id={latest_export}/"
        logger.info(
            "Found latest export: %s (modified: %s)", prefix, latest_time
        )
        return prefix

    logger.warning("No exports with data found")
    return None


def find_latest_execution_id(s3_client: Any, bucket: str) -> str | None:
    """Find the latest execution ID from batch bucket results folder."""
    logger.info("Finding latest execution ID in s3://%s/results/", bucket)

    try:
        response = s3_client.list_objects_v2(
            Bucket=bucket, Prefix="results/", Delimiter="/"
        )
        prefixes = response.get("CommonPrefixes", [])
        if not prefixes:
            logger.warning("No execution folders found in results/")
            return None

        execution_ids = [p["Prefix"].split("/")[1] for p in prefixes]
        execution_ids.sort(reverse=True)
        latest = execution_ids[0]
        logger.info("Found latest execution ID: %s", latest)
        return latest

    except Exception:
        logger.exception("Failed to find execution ID")
        return None


# --- Parsing Utilities ---


def parse_label_string(label_str: str) -> dict[str, Any]:
    """Parse stringified ReceiptWordLabel to dict."""
    result: dict[str, Any] = {}
    patterns = {
        "line_id": r"line_id=(\d+)",
        "word_id": r"word_id=(\d+)",
        "label": r"label='([^']+)'",
    }
    for field, pattern in patterns.items():
        match = re.search(pattern, label_str)
        if match:
            val = match.group(1)
            result[field] = (
                int(val) if field in ("line_id", "word_id") else val
            )
    return result


# --- Main Processing Functions ---


def main() -> int:
    """Main entry point."""
    args = parse_args()
    _log_startup(args)

    s3_client = boto3.client("s3")

    # Resolve parquet prefix
    parquet_prefix = _resolve_parquet_prefix(s3_client, args)
    if not parquet_prefix:
        return 1

    # Resolve execution ID
    execution_id = _resolve_execution_id(s3_client, args)
    if not execution_id:
        return 1

    # Load receipt lookup
    receipt_lookup = load_receipts_from_s3(s3_client, args.receipts_json)

    # Initialize Spark and process
    logger.info("Initializing Spark...")
    spark = SparkSession.builder.appName("VizCacheGenerator").getOrCreate()

    try:
        # Extract receipts from Parquet
        parquet_data = _extract_parquet_receipts(
            spark, s3_client, args, parquet_prefix
        )
        if not parquet_data:
            logger.error("No receipts extracted from Parquet")
            return 1

        # Build visualization receipts
        viz_receipts = _build_viz_receipts(
            s3_client,
            parquet_data,
            receipt_lookup,
            args.batch_bucket,
            execution_id,
        )

        # Write cache files
        _write_cache(
            s3_client,
            args.cache_bucket,
            viz_receipts,
            execution_id,
            parquet_prefix,
        )

    except Exception:
        logger.exception("Cache generation failed")
        return 1

    else:
        return 0

    finally:
        spark.stop()


def _log_startup(args: argparse.Namespace) -> None:
    """Log startup configuration."""
    logger.info("Starting visualization cache generation")
    logger.info("Batch bucket: s3://%s", args.batch_bucket)
    logger.info("Cache bucket: s3://%s", args.cache_bucket)
    logger.info("Receipts JSON: %s", args.receipts_json)


def _resolve_parquet_prefix(
    s3_client: Any, args: argparse.Namespace
) -> str | None:
    """Resolve and validate parquet prefix."""
    preferred_export_id = None
    parquet_prefix = args.parquet_prefix

    # Extract preferred export_id if provided
    if "export_id=" in parquet_prefix:
        match = re.search(r"export_id=([^/]+)", parquet_prefix)
        if match:
            preferred_export_id = match.group(1)
            logger.info(
                "Preferred export ID from args: %s", preferred_export_id
            )

    logger.info("Checking parquet prefix: %s", parquet_prefix)

    # Check if specified prefix has data
    prefix_has_data = False
    if parquet_prefix != "traces/":
        try:
            prefix_has_data = _prefix_has_data(
                s3_client, args.parquet_bucket, parquet_prefix
            )
            if prefix_has_data:
                logger.info("Prefix has data, using as-is")
            else:
                logger.warning("Prefix %s has no data", parquet_prefix)
        except Exception:
            logger.warning("Error checking prefix", exc_info=True)

    # Find latest if needed
    if not prefix_has_data:
        detected = find_latest_export_prefix(
            s3_client, args.parquet_bucket, preferred_export_id
        )
        if detected:
            parquet_prefix = detected
        else:
            logger.error("Could not find any export with data")
            return None

    logger.info("Parquet: s3://%s/%s", args.parquet_bucket, parquet_prefix)
    return parquet_prefix


def _resolve_execution_id(
    s3_client: Any, args: argparse.Namespace
) -> str | None:
    """Resolve execution ID from args or bucket."""
    execution_id = args.execution_id
    if not execution_id:
        execution_id = find_latest_execution_id(s3_client, args.batch_bucket)
        if not execution_id:
            logger.error("Could not find execution ID in batch bucket")
            return None

    logger.info("Using execution ID: %s", execution_id)
    return execution_id


def _extract_parquet_receipts(
    spark: SparkSession,
    _s3_client: Any,  # Unused - kept for API compatibility
    args: argparse.Namespace,
    parquet_prefix: str,
) -> list[dict[str, Any]]:
    """Extract receipt data from Parquet files using LangSmithSparkProcessor.

    Uses the shared processor for consistent Parquet handling:
    - Automatic s3:// to s3a:// conversion
    - Proper nanosecond timestamp handling
    - Flexible schema (handles missing columns)
    """
    # Build full parquet path
    parquet_path = f"s3://{args.parquet_bucket}/{parquet_prefix}"

    # Use processor for consistent Parquet reading
    processor = LangSmithSparkProcessor(spark)
    df = processor.read_parquet(parquet_path)

    # Extract LangGraph receipts using processor
    langgraph_data = processor.extract_langgraph_receipts(df)

    # Parse each row into receipt format
    receipts = []
    for row in langgraph_data:
        receipt = _parse_langgraph_row_dict(row)
        if receipt:
            receipts.append(receipt)

    logger.info("Extracted %d receipts from Parquet", len(receipts))
    return receipts


def _parse_langgraph_row_dict(row: dict[str, Any]) -> dict[str, Any] | None:
    """Parse a LangGraph row dict into receipt data.

    Args:
        row: Dict with 'outputs' key containing JSON string or parsed dict.

    Returns:
        Receipt data dict or None if invalid.
    """
    raw_outputs = row.get("outputs")
    if not raw_outputs:
        return None

    if isinstance(raw_outputs, str):
        try:
            outputs = json.loads(raw_outputs)
        except json.JSONDecodeError:
            logger.warning("Malformed JSON in outputs, skipping row")
            return None
    else:
        outputs = raw_outputs
    if not outputs:
        return None

    image_id = outputs.get("image_id")
    receipt_id = outputs.get("receipt_id")
    if not image_id or receipt_id is None:
        return None

    words = outputs.get("words", [])
    labels_raw = outputs.get("labels", [])
    labels_lookup = _build_labels_lookup(labels_raw)

    return {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "words": words,
        "labels_lookup": labels_lookup,
    }


def _build_labels_lookup(
    labels_raw: list[str],
) -> dict[tuple[int, int], str | None]:
    """Build (line_id, word_id) -> label lookup from raw label strings."""
    lookup: dict[tuple[int, int], str | None] = {}
    for label_str in labels_raw:
        if isinstance(label_str, str):
            parsed = parse_label_string(label_str)
            if "line_id" in parsed and "word_id" in parsed:
                lookup[(parsed["line_id"], parsed["word_id"])] = parsed.get(
                    "label"
                )
    return lookup


def _build_viz_receipts(
    s3_client: Any,
    parquet_data: list[dict[str, Any]],
    receipt_lookup: dict[tuple[str, int], dict[str, Any]],
    batch_bucket: str,
    execution_id: str,
) -> list[dict[str, Any]]:
    """Build visualization receipts from parquet and S3 data."""
    logger.info(
        "Building visualization data for %d receipts...", len(parquet_data)
    )
    viz_receipts = []

    for data in parquet_data:
        receipt = _build_single_viz_receipt(
            s3_client, data, receipt_lookup, batch_bucket, execution_id
        )
        if receipt:
            viz_receipts.append(receipt)

    # Sort by issues (most issues first)
    viz_receipts.sort(key=lambda r: -r["issues_found"])

    logger.info("Built %d visualization receipts", len(viz_receipts))
    return viz_receipts


def _generate_cdn_keys(base_cdn_key: str) -> dict[str, str | None]:
    """Generate all CDN format keys from base JPEG key.

    Args:
        base_cdn_key: Base JPEG key like 'assets/uuid_RECEIPT_00001.jpg'

    Returns:
        Dict with all CDN variants: cdn_s3_key, cdn_webp_s3_key, cdn_avif_s3_key,
        cdn_medium_s3_key, cdn_medium_webp_s3_key, cdn_medium_avif_s3_key
    """
    # Handle common JPEG extensions (case-insensitive)
    lower_key = base_cdn_key.lower()
    if lower_key.endswith(".jpeg"):
        base = base_cdn_key[:-5]  # Remove .jpeg
    elif lower_key.endswith(".jpg"):
        base = base_cdn_key[:-4]  # Remove .jpg
    else:
        return {
            "cdn_s3_key": base_cdn_key,
            "cdn_webp_s3_key": None,
            "cdn_avif_s3_key": None,
            "cdn_medium_s3_key": None,
            "cdn_medium_webp_s3_key": None,
            "cdn_medium_avif_s3_key": None,
        }

    return {
        "cdn_s3_key": base_cdn_key,
        "cdn_webp_s3_key": f"{base}.webp",
        "cdn_avif_s3_key": f"{base}.avif",
        "cdn_medium_s3_key": f"{base}_medium.jpg",
        "cdn_medium_webp_s3_key": f"{base}_medium.webp",
        "cdn_medium_avif_s3_key": f"{base}_medium.avif",
    }


def _build_single_viz_receipt(
    s3_client: Any,
    parquet_data: dict[str, Any],
    receipt_lookup: dict[tuple[str, int], dict[str, Any]],
    batch_bucket: str,
    execution_id: str,
) -> dict[str, Any] | None:
    """Build a single VizCacheReceipt from parquet data."""
    image_id, receipt_id = parquet_data["image_id"], parquet_data["receipt_id"]
    receipt_data = receipt_lookup.get((image_id, receipt_id))
    if not receipt_data:
        return None

    # Load and build results
    results = _load_all_results(
        s3_client, batch_bucket, execution_id, image_id, receipt_id
    )
    if results is None:
        return None

    # Get CDN keys from lookup (fallback to generating from base key if not present)
    cdn_s3_key = receipt_data.get("cdn_s3_key")
    if not cdn_s3_key:
        return None

    # Get CDN keys from receipt data (populated from DynamoDB)
    cdn_webp_s3_key = receipt_data.get("cdn_webp_s3_key")
    cdn_avif_s3_key = receipt_data.get("cdn_avif_s3_key")
    cdn_medium_s3_key = receipt_data.get("cdn_medium_s3_key")
    cdn_medium_webp_s3_key = receipt_data.get("cdn_medium_webp_s3_key")
    cdn_medium_avif_s3_key = receipt_data.get("cdn_medium_avif_s3_key")

    # Generate missing CDN keys from base if needed
    generated = _generate_cdn_keys(cdn_s3_key)
    cdn_webp_s3_key = cdn_webp_s3_key or generated["cdn_webp_s3_key"]
    cdn_avif_s3_key = cdn_avif_s3_key or generated["cdn_avif_s3_key"]
    cdn_medium_s3_key = cdn_medium_s3_key or generated["cdn_medium_s3_key"]
    cdn_medium_webp_s3_key = (
        cdn_medium_webp_s3_key or generated["cdn_medium_webp_s3_key"]
    )
    cdn_medium_avif_s3_key = (
        cdn_medium_avif_s3_key or generated["cdn_medium_avif_s3_key"]
    )

    # Get dimensions from lookup (with fallback for legacy format)
    width = receipt_data.get("width", 800)
    height = receipt_data.get("height", 2400)

    return VizCacheReceipt(
        image_id=image_id,
        receipt_id=receipt_id,
        merchant_name=results["merchant_name"],
        execution_id=execution_id,
        issues_found=_count_total_issues(
            results["geometric"],
            results["currency"],
            results["metadata"],
            results["financial"],
        ),
        words=_build_words_with_labels(parquet_data),
        geometric=results["geometric"],
        currency=results["currency"],
        metadata=results["metadata"],
        financial=results["financial"],
        cdn_s3_key=cdn_s3_key,
        cdn_webp_s3_key=cdn_webp_s3_key,
        cdn_avif_s3_key=cdn_avif_s3_key,
        cdn_medium_s3_key=cdn_medium_s3_key,
        cdn_medium_webp_s3_key=cdn_medium_webp_s3_key,
        cdn_medium_avif_s3_key=cdn_medium_avif_s3_key,
        width=width,
        height=height,
    ).model_dump()


def _load_all_results(
    s3_client: Any,
    bucket: str,
    execution_id: str,
    image_id: str,
    receipt_id: int,
) -> dict[str, Any] | None:
    """Load all S3 results and build typed results."""
    currency = load_s3_result(
        s3_client, bucket, "currency", execution_id, image_id, receipt_id
    )
    if not currency:
        return None

    return {
        "currency": _build_evaluator_result(currency),
        "metadata": _build_evaluator_result(
            load_s3_result(
                s3_client,
                bucket,
                "metadata",
                execution_id,
                image_id,
                receipt_id,
            )
        ),
        "financial": _build_evaluator_result(
            load_s3_result(
                s3_client,
                bucket,
                "financial",
                execution_id,
                image_id,
                receipt_id,
            )
        ),
        "geometric": _build_geometric_result(
            load_s3_result(
                s3_client,
                bucket,
                "results",
                execution_id,
                image_id,
                receipt_id,
            )
        ),
        "merchant_name": currency.get("merchant_name", "Unknown"),
    }


def _build_evaluator_result(
    s3_result: dict[str, Any] | None,
) -> EvaluatorResult:
    """Build EvaluatorResult from S3 result dict."""
    if not s3_result:
        return EvaluatorResult()

    decisions_dict = s3_result.get("decisions", {})
    return EvaluatorResult(
        decisions=DecisionCounts(
            VALID=decisions_dict.get("VALID", 0),
            INVALID=decisions_dict.get("INVALID", 0),
            NEEDS_REVIEW=decisions_dict.get("NEEDS_REVIEW", 0),
        ),
        all_decisions=s3_result.get("all_decisions", []),
        duration_seconds=s3_result.get("duration_seconds", 0.0),
    )


def _build_geometric_result(
    s3_result: dict[str, Any] | None,
) -> GeometricResult:
    """Build GeometricResult from S3 result dict."""
    if not s3_result:
        return GeometricResult()

    return GeometricResult(
        issues_found=s3_result.get("issues_found", 0),
        issues=s3_result.get("issues", []),
        duration_seconds=s3_result.get("duration_seconds", 0.0),
    )


def _build_words_with_labels(
    parquet_data: dict[str, Any],
) -> list[WordWithLabel]:
    """Build word list with labels from parquet data."""
    words = []
    labels_lookup = parquet_data.get("labels_lookup", {})

    for w in parquet_data.get("words", []):
        line_id = w.get("line_id")
        word_id = w.get("word_id")
        label = labels_lookup.get((line_id, word_id))
        bbox_data = w.get("bounding_box", {})

        word = WordWithLabel(
            text=w.get("text", ""),
            label=label,
            line_id=line_id,
            word_id=word_id,
            bbox=BoundingBox(
                x=bbox_data.get("x", 0.0),
                y=bbox_data.get("y", 0.0),
                width=bbox_data.get("width", 0.0),
                height=bbox_data.get("height", 0.0),
            ),
        )
        words.append(word)

    return words


def _count_total_issues(
    geometric: GeometricResult,
    currency: EvaluatorResult,
    metadata: EvaluatorResult,
    financial: EvaluatorResult,
) -> int:
    """Count total issues across all evaluators."""
    return (
        geometric.issues_found
        + currency.decisions.INVALID
        + currency.decisions.NEEDS_REVIEW
        + metadata.decisions.INVALID
        + metadata.decisions.NEEDS_REVIEW
        + financial.decisions.INVALID
        + financial.decisions.NEEDS_REVIEW
    )


def _write_cache(
    s3_client: Any,
    bucket: str,
    receipts: list[dict[str, Any]],
    execution_id: str,
    parquet_prefix: str,
) -> None:
    """Write individual receipt files + metadata to S3.

    Storage pattern (like LayoutLM):
    - receipts/receipt-{image_id}-{receipt_id}.json (one per receipt)
    - metadata.json (pool stats)
    - latest.json (version pointer for compatibility)
    """
    timestamp = datetime.now(timezone.utc)
    cache_version = timestamp.strftime("%Y%m%d-%H%M%S")
    receipts_prefix = "receipts/"

    # Write each receipt as individual file (parallel for better performance)
    logger.info(
        "Writing %d individual receipt files to s3://%s/%s",
        len(receipts),
        bucket,
        receipts_prefix,
    )

    def upload_receipt(receipt: dict[str, Any]) -> str:
        """Upload a single receipt and return its key."""
        image_id = receipt.get("image_id", "unknown")
        receipt_id = receipt.get("receipt_id", 0)
        key = f"{receipts_prefix}receipt-{image_id}-{receipt_id}.json"
        s3_client.put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(receipt, default=str),
            ContentType="application/json",
        )
        return key

    # Parallel upload with progress logging
    # Map futures to receipts for error reporting
    uploaded_count = 0
    failed_count = 0
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_receipt = {
            executor.submit(upload_receipt, r): r for r in receipts
        }
        for future in as_completed(future_to_receipt):
            receipt = future_to_receipt[future]
            try:
                future.result()
                uploaded_count += 1
                if uploaded_count % 50 == 0:
                    logger.info(
                        "Uploaded %d/%d receipts",
                        uploaded_count,
                        len(receipts),
                    )
            except Exception:
                failed_count += 1
                logger.exception(
                    "Failed to upload receipt %s_%d",
                    receipt.get("image_id", "unknown"),
                    receipt.get("receipt_id", 0),
                )
                # Continue uploading remaining receipts rather than aborting
                # The job is idempotent so partial uploads are okay

    if failed_count > 0:
        logger.warning(
            "Completed with %d failures out of %d receipts",
            failed_count,
            len(receipts),
        )

    # Write metadata.json with pool stats
    metadata = {
        "version": cache_version,
        "execution_id": execution_id,
        "parquet_prefix": parquet_prefix,
        "total_receipts": len(receipts),
        "receipts_with_issues": len(
            [r for r in receipts if r["issues_found"] > 0]
        ),
        "cached_at": timestamp.isoformat(),
    }
    logger.info("Writing metadata.json to s3://%s/metadata.json", bucket)
    s3_client.put_object(
        Bucket=bucket,
        Key="metadata.json",
        Body=json.dumps(metadata, indent=2),
        ContentType="application/json",
    )

    # Write latest.json pointer (for compatibility/rollback)
    latest_pointer = {
        "version": cache_version,
        "receipts_prefix": receipts_prefix,
        "metadata_key": "metadata.json",
        "updated_at": timestamp.isoformat(),
    }
    logger.info("Updating latest.json pointer")
    s3_client.put_object(
        Bucket=bucket,
        Key="latest.json",
        Body=json.dumps(latest_pointer, indent=2),
        ContentType="application/json",
    )

    logger.info("Cache generation complete!")
    logger.info("  Version: %s", cache_version)
    logger.info("  Total receipts: %d", len(receipts))
    logger.info("  Receipts with issues: %d", metadata["receipts_with_issues"])
    for r in receipts[:5]:
        logger.info(
            "  %s: %d issues, %d words",
            r["merchant_name"],
            r["issues_found"],
            len(r["words"]),
        )


if __name__ == "__main__":
    sys.exit(main())
