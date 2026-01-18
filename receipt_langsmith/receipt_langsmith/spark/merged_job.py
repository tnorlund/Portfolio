#!/usr/bin/env python3
"""EMR Serverless job entry point for combined analytics and viz-cache generation.

This unified job merges analytics (from LangSmith Parquet exports) and viz-cache
(from S3 JSON files) into a single EMR job, eliminating the need for separate
workflows and reducing LangSmith export polling overhead.

Job Types:
    analytics: Run only analytics (same as emr_job.py)
    viz-cache: Run only viz-cache generation (reads from S3 JSON files)
    all: Run both analytics and viz-cache

Usage:
    spark-submit \\
        --conf spark.executor.memory=4g \\
        --conf spark.executor.cores=2 \\
        merged_job.py \\
        --job-type all \\
        --parquet-input s3://langsmith-export/traces/ \\
        --analytics-output s3://analytics-bucket/ \\
        --batch-bucket label-evaluator-batch-bucket \\
        --cache-bucket viz-cache-bucket \\
        --execution-id abc123

Key Differences from viz_cache_job.py:
    - Reads words/labels from S3 JSON files (data/{execution_id}/*.json)
    - Reads evaluation results from S3 JSON (unified/{execution_id}/*.json)
    - No longer depends on LangSmith Parquet exports for viz-cache
    - Analytics still uses Parquet for full trace analysis
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Merged analytics and viz-cache job for EMR Serverless"
    )

    parser.add_argument(
        "--job-type",
        choices=["analytics", "viz-cache", "all"],
        default="all",
        help="Type of job to run (default: all)",
    )

    # Analytics arguments
    parser.add_argument(
        "--parquet-input",
        help="S3 path for Parquet traces (required for analytics)",
    )
    parser.add_argument(
        "--analytics-output",
        help="S3 output path for analytics results (required for analytics)",
    )
    parser.add_argument(
        "--partition-by-merchant",
        action="store_true",
        help="Partition output by merchant name",
    )
    parser.add_argument(
        "--partition-by-date",
        action="store_true",
        help="Partition output by date",
    )

    # Viz-cache arguments
    parser.add_argument(
        "--batch-bucket",
        help="S3 bucket with receipt data and results (required for viz-cache)",
    )
    parser.add_argument(
        "--cache-bucket",
        help="S3 bucket to write viz-cache files (required for viz-cache)",
    )
    parser.add_argument(
        "--execution-id",
        help="Execution ID for viz-cache (required for viz-cache)",
    )
    parser.add_argument(
        "--receipts-json",
        help="S3 path to receipts-lookup.json (optional for viz-cache)",
    )

    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    """Validate required arguments based on job type."""
    if args.job_type in ("analytics", "all"):
        if not args.parquet_input:
            raise ValueError("--parquet-input required for analytics")
        if not args.analytics_output:
            raise ValueError("--analytics-output required for analytics")

    if args.job_type in ("viz-cache", "all"):
        if not args.batch_bucket:
            raise ValueError("--batch-bucket required for viz-cache")
        if not args.cache_bucket:
            raise ValueError("--cache-bucket required for viz-cache")
        if not args.execution_id:
            raise ValueError("--execution-id required for viz-cache")


# =============================================================================
# Analytics Functions (from emr_job.py)
# =============================================================================


def run_analytics(
    spark: SparkSession, args: argparse.Namespace
) -> None:
    """Run analytics job using LangSmithSparkProcessor."""
    from receipt_langsmith.spark.processor import LangSmithSparkProcessor

    processor = LangSmithSparkProcessor(spark)

    logger.info("Reading Parquet data from %s...", args.parquet_input)
    df = processor.read_parquet(args.parquet_input)

    logger.info("Parsing JSON fields...")
    parsed = processor.parse_json_fields(df)
    parsed.cache()

    try:
        # Job analytics
        logger.info("Computing job analytics...")
        job_stats = processor.compute_job_analytics(parsed)
        processor.write_analytics(
            job_stats, f"{args.analytics_output}/job_analytics/"
        )

        job_by_merchant = processor.compute_job_analytics_by_merchant(parsed)
        partition_by = ["merchant_name"] if args.partition_by_merchant else None
        processor.write_analytics(
            job_by_merchant,
            f"{args.analytics_output}/job_analytics_by_merchant/",
            partition_by=partition_by,
        )

        # Receipt analytics
        logger.info("Computing receipt analytics...")
        receipt_stats = processor.compute_receipt_analytics(parsed)
        processor.write_analytics(
            receipt_stats,
            f"{args.analytics_output}/receipt_analytics/",
            partition_by=partition_by,
        )

        # Step timing
        logger.info("Computing step timing...")
        timing = processor.compute_step_timing(parsed)
        processor.write_analytics(
            timing, f"{args.analytics_output}/step_timing/"
        )

        # Decision analysis
        logger.info("Computing decision analysis...")
        decisions = processor.compute_decision_analysis(parsed)
        processor.write_analytics(
            decisions,
            f"{args.analytics_output}/decision_analysis/",
            partition_by=partition_by,
        )

        # Token usage
        logger.info("Computing token usage...")
        tokens = processor.compute_token_usage(parsed)
        partition_by = ["date"] if args.partition_by_date else None
        processor.write_analytics(
            tokens,
            f"{args.analytics_output}/token_usage/",
            partition_by=partition_by,
        )

        logger.info("Analytics complete!")

    finally:
        parsed.unpersist()


# =============================================================================
# Viz-Cache Functions (from S3 JSON files)
# =============================================================================


def load_receipts_lookup(
    s3_client: Any, receipts_json_path: str | None
) -> dict[tuple[str, int], dict[str, Any]]:
    """Load receipt lookup from S3 JSON file if provided."""
    if not receipts_json_path:
        return {}

    logger.info("Loading receipts lookup from %s", receipts_json_path)

    if not receipts_json_path.startswith("s3://"):
        raise ValueError(f"Invalid S3 path: {receipts_json_path}")

    path = receipts_json_path[5:]
    bucket, key = path.split("/", 1)
    response = s3_client.get_object(Bucket=bucket, Key=key)
    raw_lookup = json.loads(response["Body"].read().decode("utf-8"))

    lookup: dict[tuple[str, int], dict[str, Any]] = {}
    for composite_key, receipt_data in raw_lookup.items():
        parts = composite_key.rsplit("_", 1)
        if len(parts) == 2:
            image_id, receipt_id_str = parts
            try:
                if isinstance(receipt_data, str):
                    lookup[(image_id, int(receipt_id_str))] = {
                        "cdn_s3_key": receipt_data,
                        "width": 800,
                        "height": 2400,
                    }
                else:
                    lookup[(image_id, int(receipt_id_str))] = receipt_data
            except ValueError:
                continue

    logger.info("Loaded %d receipts from lookup", len(lookup))
    return lookup


def list_s3_json_files(
    s3_client: Any, bucket: str, prefix: str
) -> list[str]:
    """List all JSON files under an S3 prefix."""
    keys = []
    paginator = s3_client.get_paginator("list_objects_v2")

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".json"):
                keys.append(key)

    return keys


def load_receipt_data_from_s3(
    s3_client: Any, bucket: str, execution_id: str
) -> list[dict[str, Any]]:
    """Load receipt data (words, labels, place) from S3 JSON files.

    Reads from: data/{execution_id}/*.json
    Each file contains: image_id, receipt_id, words, labels, place
    """
    prefix = f"data/{execution_id}/"
    logger.info("Loading receipt data from s3://%s/%s", bucket, prefix)

    keys = list_s3_json_files(s3_client, bucket, prefix)
    logger.info("Found %d receipt data files", len(keys))

    receipts = []
    for key in keys:
        try:
            response = s3_client.get_object(Bucket=bucket, Key=key)
            data = json.loads(response["Body"].read().decode("utf-8"))
            receipts.append(data)
        except Exception as e:
            logger.warning("Failed to load %s: %s", key, e)

    return receipts


def load_unified_results_from_s3(
    s3_client: Any, bucket: str, execution_id: str
) -> dict[tuple[str, int], dict[str, Any]]:
    """Load unified evaluation results from S3 JSON files.

    Reads from: unified/{execution_id}/*.json
    Each file contains: image_id, receipt_id, merchant_name, decisions, issues, etc.

    Returns:
        Dict mapping (image_id, receipt_id) -> result data
    """
    prefix = f"unified/{execution_id}/"
    logger.info("Loading unified results from s3://%s/%s", bucket, prefix)

    keys = list_s3_json_files(s3_client, bucket, prefix)
    logger.info("Found %d unified result files", len(keys))

    results: dict[tuple[str, int], dict[str, Any]] = {}
    for key in keys:
        try:
            response = s3_client.get_object(Bucket=bucket, Key=key)
            data = json.loads(response["Body"].read().decode("utf-8"))
            image_id = data.get("image_id")
            receipt_id = data.get("receipt_id")
            if image_id and receipt_id is not None:
                results[(image_id, receipt_id)] = data
        except Exception as e:
            logger.warning("Failed to load %s: %s", key, e)

    return results


def build_words_with_labels(
    words: list[dict[str, Any]], labels: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Build word list with labels."""
    # Build label lookup: (line_id, word_id) -> label
    label_lookup: dict[tuple[int, int], str] = {}
    for label in labels:
        line_id = label.get("line_id")
        word_id = label.get("word_id")
        label_text = label.get("label")
        if line_id is not None and word_id is not None:
            label_lookup[(line_id, word_id)] = label_text

    result = []
    for word in words:
        line_id = word.get("line_id")
        word_id = word.get("word_id")
        bbox = word.get("bounding_box", {})

        word_with_label = {
            "text": word.get("text", ""),
            "label": label_lookup.get((line_id, word_id)),
            "line_id": line_id,
            "word_id": word_id,
            "bbox": {
                "x": bbox.get("x", 0.0),
                "y": bbox.get("y", 0.0),
                "width": bbox.get("width", 0.0),
                "height": bbox.get("height", 0.0),
            },
        }
        result.append(word_with_label)

    return result


def generate_cdn_keys(base_cdn_key: str) -> dict[str, str | None]:
    """Generate all CDN format keys from base JPEG key."""
    lower_key = base_cdn_key.lower()
    if lower_key.endswith(".jpeg"):
        base = base_cdn_key[:-5]
    elif lower_key.endswith(".jpg"):
        base = base_cdn_key[:-4]
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


def count_issues(result: dict[str, Any]) -> int:
    """Count total issues from unified result."""
    total = 0

    # Geometric issues
    total += result.get("geometric_issues_found", 0)

    # Decision-based issues (INVALID + NEEDS_REVIEW)
    for key in ("currency_decisions", "metadata_decisions", "financial_decisions"):
        decisions = result.get(key, {})
        total += decisions.get("INVALID", 0)
        total += decisions.get("NEEDS_REVIEW", 0)

    return total


def build_viz_receipt(
    receipt_data: dict[str, Any],
    unified_result: dict[str, Any] | None,
    receipt_lookup: dict[tuple[str, int], dict[str, Any]],
    execution_id: str,
) -> dict[str, Any] | None:
    """Build a single viz-cache receipt entry."""
    image_id = receipt_data.get("image_id")
    receipt_id = receipt_data.get("receipt_id")

    if not image_id or receipt_id is None:
        return None

    words = receipt_data.get("words", [])
    labels = receipt_data.get("labels", [])

    # Build words with labels
    words_with_labels = build_words_with_labels(words, labels)

    # Get CDN keys from lookup
    lookup_data = receipt_lookup.get((image_id, receipt_id), {})
    cdn_s3_key = lookup_data.get("cdn_s3_key")

    if cdn_s3_key:
        cdn_keys = generate_cdn_keys(cdn_s3_key)
        # Override with lookup values if present
        for key in cdn_keys:
            if lookup_data.get(key):
                cdn_keys[key] = lookup_data[key]
    else:
        cdn_keys = {
            "cdn_s3_key": None,
            "cdn_webp_s3_key": None,
            "cdn_avif_s3_key": None,
            "cdn_medium_s3_key": None,
            "cdn_medium_webp_s3_key": None,
            "cdn_medium_avif_s3_key": None,
        }

    # Get dimensions
    width = lookup_data.get("width", 800)
    height = lookup_data.get("height", 2400)

    # Get merchant name and issues from unified result
    merchant_name = "Unknown"
    issues_found = 0
    currency = {"decisions": {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0}}
    metadata = {"decisions": {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0}}
    financial = {"decisions": {"VALID": 0, "INVALID": 0, "NEEDS_REVIEW": 0}}
    geometric = {"issues_found": 0, "issues": []}

    if unified_result:
        merchant_name = unified_result.get("merchant_name", "Unknown")
        issues_found = count_issues(unified_result)

        # Currency decisions
        currency_decisions = unified_result.get("currency_decisions", {})
        currency = {
            "decisions": {
                "VALID": currency_decisions.get("VALID", 0),
                "INVALID": currency_decisions.get("INVALID", 0),
                "NEEDS_REVIEW": currency_decisions.get("NEEDS_REVIEW", 0),
            },
            "all_decisions": unified_result.get("currency_all_decisions", []),
            "duration_seconds": unified_result.get("currency_duration_seconds", 0),
        }

        # Metadata decisions
        metadata_decisions = unified_result.get("metadata_decisions", {})
        metadata = {
            "decisions": {
                "VALID": metadata_decisions.get("VALID", 0),
                "INVALID": metadata_decisions.get("INVALID", 0),
                "NEEDS_REVIEW": metadata_decisions.get("NEEDS_REVIEW", 0),
            },
            "all_decisions": unified_result.get("metadata_all_decisions", []),
            "duration_seconds": unified_result.get("metadata_duration_seconds", 0),
        }

        # Financial decisions
        financial_decisions = unified_result.get("financial_decisions", {})
        financial = {
            "decisions": {
                "VALID": financial_decisions.get("VALID", 0),
                "INVALID": financial_decisions.get("INVALID", 0),
                "NEEDS_REVIEW": financial_decisions.get("NEEDS_REVIEW", 0),
            },
            "all_decisions": unified_result.get("financial_all_decisions", []),
            "duration_seconds": unified_result.get("financial_duration_seconds", 0),
        }

        # Geometric issues
        geometric = {
            "issues_found": unified_result.get("geometric_issues_found", 0),
            "issues": unified_result.get("geometric_issues", []),
            "duration_seconds": unified_result.get("geometric_duration_seconds", 0),
        }

    return {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "merchant_name": merchant_name,
        "execution_id": execution_id,
        "issues_found": issues_found,
        "words": words_with_labels,
        "geometric": geometric,
        "currency": currency,
        "metadata": metadata,
        "financial": financial,
        **cdn_keys,
        "width": width,
        "height": height,
    }


def write_viz_cache(
    s3_client: Any,
    bucket: str,
    receipts: list[dict[str, Any]],
    execution_id: str,
) -> None:
    """Write viz-cache files to S3."""
    timestamp = datetime.now(timezone.utc)
    cache_version = timestamp.strftime("%Y%m%d-%H%M%S")
    receipts_prefix = "receipts/"

    logger.info(
        "Writing %d receipt files to s3://%s/%s", len(receipts), bucket, receipts_prefix
    )

    def upload_receipt(receipt: dict[str, Any]) -> str:
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

    uploaded = 0
    failed = 0
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(upload_receipt, r): r for r in receipts}
        for future in as_completed(futures):
            try:
                future.result()
                uploaded += 1
                if uploaded % 50 == 0:
                    logger.info("Uploaded %d/%d receipts", uploaded, len(receipts))
            except Exception:
                failed += 1
                receipt = futures[future]
                logger.exception(
                    "Failed to upload %s_%s",
                    receipt.get("image_id"),
                    receipt.get("receipt_id"),
                )

    if failed > 0:
        logger.warning("Completed with %d failures out of %d", failed, len(receipts))

    # Write metadata.json
    metadata = {
        "version": cache_version,
        "execution_id": execution_id,
        "total_receipts": len(receipts),
        "receipts_with_issues": len([r for r in receipts if r["issues_found"] > 0]),
        "cached_at": timestamp.isoformat(),
    }
    s3_client.put_object(
        Bucket=bucket,
        Key="metadata.json",
        Body=json.dumps(metadata, indent=2),
        ContentType="application/json",
    )

    # Write latest.json pointer
    latest = {
        "version": cache_version,
        "receipts_prefix": receipts_prefix,
        "metadata_key": "metadata.json",
        "updated_at": timestamp.isoformat(),
    }
    s3_client.put_object(
        Bucket=bucket,
        Key="latest.json",
        Body=json.dumps(latest, indent=2),
        ContentType="application/json",
    )

    logger.info("Viz-cache complete! Version: %s, Receipts: %d", cache_version, len(receipts))


def run_viz_cache(args: argparse.Namespace) -> None:
    """Run viz-cache generation from S3 JSON files."""
    s3_client = boto3.client("s3")

    # Load receipts lookup (optional)
    receipt_lookup = load_receipts_lookup(s3_client, args.receipts_json)

    # Load receipt data from S3 JSON files
    receipt_data_list = load_receipt_data_from_s3(
        s3_client, args.batch_bucket, args.execution_id
    )

    if not receipt_data_list:
        logger.error("No receipt data found in data/%s/", args.execution_id)
        return

    # Load unified results
    unified_results = load_unified_results_from_s3(
        s3_client, args.batch_bucket, args.execution_id
    )

    # Build viz receipts
    logger.info("Building viz receipts for %d receipts...", len(receipt_data_list))
    viz_receipts = []
    for receipt_data in receipt_data_list:
        image_id = receipt_data.get("image_id")
        receipt_id = receipt_data.get("receipt_id")

        unified_result = unified_results.get((image_id, receipt_id))

        viz_receipt = build_viz_receipt(
            receipt_data, unified_result, receipt_lookup, args.execution_id
        )
        if viz_receipt:
            viz_receipts.append(viz_receipt)

    # Sort by issues (most issues first)
    viz_receipts.sort(key=lambda r: -r["issues_found"])

    logger.info("Built %d viz receipts", len(viz_receipts))

    # Write to S3
    write_viz_cache(s3_client, args.cache_bucket, viz_receipts, args.execution_id)


# =============================================================================
# Main Entry Point
# =============================================================================


def main() -> int:
    """Main entry point."""
    args = parse_args()

    logger.info("Starting merged job")
    logger.info("Job type: %s", args.job_type)

    try:
        validate_args(args)
    except ValueError as e:
        logger.error("Argument validation failed: %s", e)
        return 1

    spark = None

    try:
        # Run analytics if requested
        if args.job_type in ("analytics", "all"):
            logger.info("Starting analytics phase...")
            spark = SparkSession.builder.appName(
                f"MergedJob-analytics-{args.job_type}"
            ).getOrCreate()
            run_analytics(spark, args)
            logger.info("Analytics phase complete")

        # Run viz-cache if requested
        if args.job_type in ("viz-cache", "all"):
            logger.info("Starting viz-cache phase...")
            run_viz_cache(args)
            logger.info("Viz-cache phase complete")

        logger.info("All phases complete!")
        return 0

    except Exception:
        logger.exception("Job failed")
        return 1

    finally:
        if spark:
            spark.stop()


if __name__ == "__main__":
    sys.exit(main())
