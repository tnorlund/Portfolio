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
        --receipts-json s3://cache-bucket/receipts-lookup.json \\
        --max-receipts 10

Note: If --parquet-prefix is not provided, the latest export is auto-detected.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import datetime, timezone
from typing import Any

import boto3
from pyspark.sql import SparkSession
from pyspark.sql.functions import col

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


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
        help="S3 path to receipts-lookup.json (e.g., s3://bucket/receipts-lookup.json)",
    )
    parser.add_argument(
        "--max-receipts",
        type=int,
        default=10,
        help="Maximum number of receipts to include (default: 10)",
    )
    parser.add_argument(
        "--execution-id",
        default=None,
        help="Execution ID to filter batch bucket results (optional)",
    )

    return parser.parse_args()


def load_receipts_from_s3(s3_client: Any, receipts_json_path: str) -> dict[tuple[str, int], str]:
    """Load receipt lookup from S3 JSON file.

    The JSON file is a dict mapping "{image_id}_{receipt_id}" -> cdn_key,
    pre-exported by a Lambda that queries DynamoDB.

    Returns:
        dict mapping (image_id, receipt_id) -> cdn_key
    """
    logger.info("Loading receipts from %s", receipts_json_path)

    # Parse S3 path: s3://bucket/key
    if receipts_json_path.startswith("s3://"):
        path = receipts_json_path[5:]
        bucket, key = path.split("/", 1)
    else:
        raise ValueError(f"Invalid S3 path: {receipts_json_path}")

    response = s3_client.get_object(Bucket=bucket, Key=key)
    raw_lookup = json.loads(response["Body"].read().decode("utf-8"))

    # Convert "{image_id}_{receipt_id}" -> (image_id, receipt_id)
    lookup = {}
    for composite_key, cdn_key in raw_lookup.items():
        parts = composite_key.rsplit("_", 1)
        if len(parts) == 2:
            image_id, receipt_id_str = parts
            try:
                receipt_id = int(receipt_id_str)
                lookup[(image_id, receipt_id)] = cdn_key
            except ValueError:
                continue

    logger.info("Loaded %d receipts from S3", len(lookup))
    return lookup


def find_latest_export_prefix(s3_client: Any, bucket: str, preferred_export_id: str | None = None) -> str | None:
    """Find the latest LangSmith export prefix in the bucket.

    LangSmith exports are stored with paths like:
    - traces/export_id=xxx/tenant_id=yyy/session_id=zzz/runs/year=.../file.parquet

    Args:
        s3_client: Boto3 S3 client
        bucket: S3 bucket name
        preferred_export_id: If specified, try this export first before falling back

    Returns:
        S3 prefix with parquet files, or None if not found
    """
    logger.info("Finding latest export in s3://%s/traces/", bucket)

    try:
        # List at traces/ to find export_id= prefixes
        response = s3_client.list_objects_v2(
            Bucket=bucket, Prefix="traces/", Delimiter="/"
        )
        prefixes = response.get("CommonPrefixes", [])

        if not prefixes:
            logger.warning("No export folders found in traces/")
            return None

        # Extract export IDs from paths like "traces/export_id=xxx/"
        export_ids = []
        for p in prefixes:
            prefix = p["Prefix"]
            # Extract export_id value from "traces/export_id=xxx/"
            if "export_id=" in prefix:
                export_id = prefix.split("export_id=")[1].rstrip("/")
                if export_id:
                    export_ids.append(export_id)

        if not export_ids:
            logger.warning("No valid export IDs found")
            return None

        logger.info("Found %d exports: %s", len(export_ids), export_ids[:5])

        # If preferred export ID specified, check if it has data
        if preferred_export_id and preferred_export_id in export_ids:
            check_prefix = f"traces/export_id={preferred_export_id}/"
            check_response = s3_client.list_objects_v2(
                Bucket=bucket,
                Prefix=check_prefix,
                MaxKeys=1,
            )
            if check_response.get("Contents"):
                logger.info("Using preferred export: %s", preferred_export_id)
                return check_prefix

            logger.warning(
                "Preferred export %s has no data, falling back to latest",
                preferred_export_id
            )

        # Find the most recent export by checking modification times
        latest_export = None
        latest_time = None

        for export_id in export_ids:
            check_prefix = f"traces/export_id={export_id}/"
            check_response = s3_client.list_objects_v2(
                Bucket=bucket,
                Prefix=check_prefix,
                MaxKeys=1,
            )
            if check_response.get("Contents"):
                obj = check_response["Contents"][0]
                mod_time = obj.get("LastModified")
                if latest_time is None or mod_time > latest_time:
                    latest_time = mod_time
                    latest_export = export_id

        if latest_export:
            prefix = f"traces/export_id={latest_export}/"
            logger.info("Found latest export: %s (modified: %s)", prefix, latest_time)
            return prefix

        logger.warning("No exports with data found")
        return None

    except Exception as e:
        logger.error("Failed to find latest export: %s", e)
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

        # Sort by name (execution IDs are typically timestamp-based)
        execution_ids = [p["Prefix"].split("/")[1] for p in prefixes]
        execution_ids.sort(reverse=True)

        latest = execution_ids[0]
        logger.info("Found latest execution ID: %s", latest)
        return latest

    except Exception as e:
        logger.error("Failed to find execution ID: %s", e)
        return None


def load_s3_result(
    s3_client: Any, bucket: str, result_type: str, execution_id: str, image_id: str, receipt_id: int
) -> dict | None:
    """Load result JSON from S3 batch bucket."""
    key = f"{result_type}/{execution_id}/{image_id}_{receipt_id}.json"

    try:
        response = s3_client.get_object(Bucket=bucket, Key=key)
        return json.loads(response["Body"].read().decode("utf-8"))
    except s3_client.exceptions.NoSuchKey:
        return None
    except Exception as e:
        logger.debug("Failed to load s3://%s/%s: %s", bucket, key, e)
        return None


def parse_label_string(label_str: str) -> dict:
    """Parse stringified ReceiptWordLabel to dict."""
    result = {}
    patterns = {
        "line_id": r"line_id=(\d+)",
        "word_id": r"word_id=(\d+)",
        "label": r"label='([^']+)'",
    }
    for field, pattern in patterns.items():
        match = re.search(pattern, label_str)
        if match:
            val = match.group(1)
            result[field] = int(val) if field in ("line_id", "word_id") else val
    return result


def main() -> int:
    """Main entry point."""
    args = parse_args()

    logger.info("Starting visualization cache generation")
    logger.info("Batch bucket: s3://%s", args.batch_bucket)
    logger.info("Cache bucket: s3://%s", args.cache_bucket)
    logger.info("Receipts JSON: %s", args.receipts_json)
    logger.info("Max receipts: %d", args.max_receipts)

    # Initialize S3 client
    s3_client = boto3.client("s3")

    # Extract preferred export_id from parquet_prefix if provided
    preferred_export_id = None
    parquet_prefix = args.parquet_prefix

    if "export_id=" in parquet_prefix:
        # Extract export_id from format: traces/export_id=xxx/
        # Allow any characters after export_id= up to the next /
        match = re.search(r"export_id=([^/]+)", parquet_prefix)
        if match:
            preferred_export_id = match.group(1)
            logger.info("Preferred export ID from args: %s", preferred_export_id)

    # Always verify the parquet prefix has data, with fallback to latest
    # This handles: default prefix, specified export with no data, or any other case
    logger.info("Checking parquet prefix: %s", parquet_prefix)

    # Check if the specified prefix actually has data
    prefix_has_data = False
    if parquet_prefix != "traces/":
        try:
            check_response = s3_client.list_objects_v2(
                Bucket=args.parquet_bucket,
                Prefix=parquet_prefix,
                MaxKeys=1,
            )
            prefix_has_data = bool(check_response.get("Contents"))
            if prefix_has_data:
                logger.info("Prefix has data, using as-is")
            else:
                logger.warning("Prefix %s has no data, will find fallback", parquet_prefix)
        except Exception as e:
            logger.warning("Error checking prefix: %s", e)

    # If prefix doesn't have data or is default, find the latest export
    if not prefix_has_data:
        detected_prefix = find_latest_export_prefix(
            s3_client, args.parquet_bucket, preferred_export_id
        )
        if detected_prefix:
            parquet_prefix = detected_prefix
        else:
            logger.error("Could not find any export with data in bucket")
            return 1

    logger.info("Parquet: s3://%s/%s", args.parquet_bucket, parquet_prefix)

    # Find execution ID if not provided
    execution_id = args.execution_id
    if not execution_id:
        execution_id = find_latest_execution_id(s3_client, args.batch_bucket)
        if not execution_id:
            logger.error("Could not find execution ID in batch bucket")
            return 1

    logger.info("Using execution ID: %s", execution_id)

    # Load receipt lookup from S3 (pre-exported by Lambda)
    receipt_lookup = load_receipts_from_s3(s3_client, args.receipts_json)

    # Initialize Spark
    logger.info("Initializing Spark...")
    spark = SparkSession.builder.appName("VizCacheGenerator").getOrCreate()

    try:
        # Read Parquet files
        # Note: S3 allows double-slash paths (traces//export_id=xxx/) but PySpark
        # normalizes paths and removes empty segments. We need to list files explicitly.
        parquet_path = f"s3://{args.parquet_bucket}/{parquet_prefix}"
        logger.info("Reading Parquet files from %s", parquet_path)

        # List all parquet files in the export folder
        logger.info("Listing parquet files...")
        parquet_files = []
        paginator = s3_client.get_paginator("list_objects_v2")

        for page in paginator.paginate(
            Bucket=args.parquet_bucket, Prefix=parquet_prefix
        ):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.endswith(".parquet"):
                    # Use s3:// protocol (EMRFS) for EMR Serverless
                    parquet_files.append(f"s3://{args.parquet_bucket}/{key}")
                    if len(parquet_files) <= 3:
                        logger.info("  File: s3://%s/%s", args.parquet_bucket, key)

        if not parquet_files:
            logger.error("No parquet files found in %s", parquet_prefix)
            return 1

        logger.info("Found %d parquet files", len(parquet_files))

        # Read parquet files by explicit paths (avoids path normalization issue)
        df = spark.read.parquet(*parquet_files)
        total_count = df.count()
        logger.info("Total traces: %d", total_count)

        # Filter for LangGraph traces (contain receipt outputs)
        langgraph_df = df.filter(col("name") == "LangGraph")
        langgraph_count = langgraph_df.count()
        logger.info("LangGraph traces: %d", langgraph_count)

        # Extract receipt data from Parquet
        logger.info("Extracting receipt data from traces...")
        langgraph_data = langgraph_df.select("outputs").collect()

        receipts_from_parquet = []
        for row in langgraph_data:
            outputs = (
                json.loads(row.outputs) if isinstance(row.outputs, str) else row.outputs
            )
            if not outputs:
                continue

            image_id = outputs.get("image_id")
            receipt_id = outputs.get("receipt_id")
            if not image_id or receipt_id is None:
                continue

            words = outputs.get("words", [])
            labels_raw = outputs.get("labels", [])
            labels_lookup = {}
            for label_str in labels_raw:
                if isinstance(label_str, str):
                    parsed = parse_label_string(label_str)
                    if "line_id" in parsed and "word_id" in parsed:
                        labels_lookup[(parsed["line_id"], parsed["word_id"])] = parsed.get(
                            "label"
                        )

            receipts_from_parquet.append(
                {
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                    "words": words,
                    "labels_lookup": labels_lookup,
                }
            )

        logger.info("Extracted %d receipts from Parquet", len(receipts_from_parquet))

        # Build visualization data
        logger.info("Building visualization data...")
        viz_receipts = []

        for parquet_data in receipts_from_parquet:
            image_id = parquet_data["image_id"]
            receipt_id = parquet_data["receipt_id"]

            # Get CDN key from receipt lookup
            cdn_key = receipt_lookup.get((image_id, receipt_id))
            if not cdn_key:
                continue

            # Load S3 results
            currency = load_s3_result(
                s3_client, args.batch_bucket, "currency", execution_id, image_id, receipt_id
            )
            metadata = load_s3_result(
                s3_client, args.batch_bucket, "metadata", execution_id, image_id, receipt_id
            )
            financial = load_s3_result(
                s3_client, args.batch_bucket, "financial", execution_id, image_id, receipt_id
            )
            geometric = load_s3_result(
                s3_client, args.batch_bucket, "results", execution_id, image_id, receipt_id
            )

            if not currency:
                continue

            merchant_name = currency.get("merchant_name", "Unknown")

            # Build words with labels
            words = []
            for w in parquet_data["words"]:
                line_id = w.get("line_id")
                word_id = w.get("word_id")
                label = parquet_data["labels_lookup"].get((line_id, word_id))
                bbox = w.get("bounding_box", {})
                words.append(
                    {
                        "text": w.get("text", ""),
                        "label": label,
                        "line_id": line_id,
                        "word_id": word_id,
                        "bbox": {
                            "x": bbox.get("x", 0),
                            "y": bbox.get("y", 0),
                            "width": bbox.get("width", 0),
                            "height": bbox.get("height", 0),
                        },
                    }
                )

            # Count issues
            issues_found = (
                (geometric or {}).get("issues_found", 0)
                + (currency or {}).get("decisions", {}).get("INVALID", 0)
                + (currency or {}).get("decisions", {}).get("NEEDS_REVIEW", 0)
                + (metadata or {}).get("decisions", {}).get("INVALID", 0)
                + (metadata or {}).get("decisions", {}).get("NEEDS_REVIEW", 0)
                + (financial or {}).get("decisions", {}).get("INVALID", 0)
                + (financial or {}).get("decisions", {}).get("NEEDS_REVIEW", 0)
            )

            viz_receipts.append(
                {
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                    "merchant_name": merchant_name,
                    "issues_found": issues_found,
                    "words": words,
                    "geometric": {
                        "issues_found": (geometric or {}).get("issues_found", 0),
                        "issues": (geometric or {}).get("issues", []),
                        "duration_seconds": (geometric or {}).get("duration_seconds", 0),
                    },
                    "currency": {
                        "decisions": (currency or {}).get("decisions", {}),
                        "all_decisions": (currency or {}).get("all_decisions", []),
                        "duration_seconds": (currency or {}).get("duration_seconds", 0),
                    },
                    "metadata": {
                        "decisions": (metadata or {}).get("decisions", {}),
                        "all_decisions": (metadata or {}).get("all_decisions", []),
                        "duration_seconds": (metadata or {}).get("duration_seconds", 0),
                    },
                    "financial": {
                        "decisions": (financial or {}).get("decisions", {}),
                        "all_decisions": (financial or {}).get("all_decisions", []),
                        "duration_seconds": (financial or {}).get("duration_seconds", 0),
                    },
                    "cdn_key": cdn_key,
                }
            )

        # Sort by issues found and select top N
        viz_receipts.sort(key=lambda r: -r["issues_found"])
        selected = viz_receipts[: args.max_receipts]

        logger.info("Built %d total receipts, selected top %d", len(viz_receipts), len(selected))

        # Build cache structure
        timestamp = datetime.now(timezone.utc)
        cache_version = timestamp.strftime("%Y%m%d-%H%M%S")

        cache = {
            "version": cache_version,
            "execution_id": execution_id,
            "parquet_prefix": parquet_prefix,
            "receipts": selected,
            "summary": {
                "total_receipts": len(selected),
                "receipts_with_issues": len([r for r in selected if r["issues_found"] > 0]),
            },
            "cached_at": timestamp.isoformat(),
        }

        # Write versioned cache file
        versioned_key = f"viz-cache-{cache_version}.json"
        logger.info("Writing versioned cache to s3://%s/%s", args.cache_bucket, versioned_key)
        s3_client.put_object(
            Bucket=args.cache_bucket,
            Key=versioned_key,
            Body=json.dumps(cache, indent=2, default=str),
            ContentType="application/json",
        )

        # Write latest.json pointer
        latest_pointer = {
            "version": cache_version,
            "cache_file": versioned_key,
            "updated_at": timestamp.isoformat(),
        }
        logger.info("Updating latest.json pointer to %s", versioned_key)
        s3_client.put_object(
            Bucket=args.cache_bucket,
            Key="latest.json",
            Body=json.dumps(latest_pointer, indent=2),
            ContentType="application/json",
        )

        logger.info("Cache generation complete!")
        logger.info("  Version: %s", cache_version)
        for r in selected[:5]:
            logger.info("  %s: %d issues, %d words", r["merchant_name"], r["issues_found"], len(r["words"]))

        return 0

    except Exception:
        logger.exception("Cache generation failed")
        return 1

    finally:
        spark.stop()


if __name__ == "__main__":
    sys.exit(main())
