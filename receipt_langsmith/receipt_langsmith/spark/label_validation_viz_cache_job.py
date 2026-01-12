#!/usr/bin/env python3
"""EMR Serverless job for Label Validation visualization cache generation.

This script generates visualization cache files from:
1. LangSmith Parquet exports from receipt-label-validation project
2. Receipt lookup JSON from S3 (CDN keys from DynamoDB, pre-exported by Lambda)

The Label Validation pipeline has a two-tier structure:
- Tier 1: ChromaDB consensus validation (fast, ~50ms/word)
- Tier 2: LLM fallback validation (for words that fail ChromaDB)

Output:
- receipts/receipt-{image_id}-{receipt_id}.json (one per receipt)
- metadata.json (pool stats and aggregate validation metrics)
- latest.json (version pointer)

Usage:
    spark-submit \\
        --conf spark.executor.memory=4g \\
        --conf spark.executor.cores=2 \\
        --conf spark.sql.legacy.parquet.nanosAsLong=true \\
        label_validation_viz_cache_job.py \\
        --parquet-bucket langsmith-export-bucket \\
        --cache-bucket viz-cache-bucket \\
        --receipts-json s3://cache-bucket/receipts-lookup.json
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
from pyspark.sql import functions as F
from pyspark.sql.types import LongType

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# --- Argument Parsing ---


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Label Validation visualization cache generator for EMR Serverless"
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
        "--cache-bucket",
        required=True,
        help="S3 bucket to write visualization cache",
    )
    parser.add_argument(
        "--receipts-json",
        required=True,
        help="S3 path to receipts-lookup.json (CDN keys from DynamoDB)",
    )
    parser.add_argument(
        "--max-receipts",
        type=int,
        default=50,
        help="Maximum receipts to include in cache (default: 50)",
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
            - words (list of word dicts with bounding boxes)
            - labels (dict mapping (line_id, word_id) -> label)
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

        export_ids = []
        for p in prefixes:
            prefix = p["Prefix"]
            if "export_id=" in prefix:
                export_id = prefix.split("export_id=")[1].rstrip("/")
                if export_id:
                    export_ids.append(export_id)

        if not export_ids:
            logger.warning("No valid export IDs found")
            return None

        logger.info("Found %d exports: %s", len(export_ids), export_ids[:5])

        # Check preferred export first
        if preferred_export_id and preferred_export_id in export_ids:
            check_prefix = f"traces/export_id={preferred_export_id}/"
            resp = s3_client.list_objects_v2(
                Bucket=bucket, Prefix=check_prefix, MaxKeys=1
            )
            if resp.get("Contents"):
                logger.info("Using preferred export: %s", preferred_export_id)
                return check_prefix

        # Find most recent export
        latest_export = None
        latest_time = None
        for export_id in export_ids:
            check_prefix = f"traces/export_id={export_id}/"
            resp = s3_client.list_objects_v2(
                Bucket=bucket, Prefix=check_prefix, MaxKeys=1
            )
            if resp.get("Contents"):
                mod_time = resp["Contents"][0].get("LastModified")
                if latest_time is None or mod_time > latest_time:
                    latest_time = mod_time
                    latest_export = export_id

        if latest_export:
            prefix = f"traces/export_id={latest_export}/"
            logger.info("Found latest export: %s (modified: %s)", prefix, latest_time)
            return prefix

        logger.warning("No exports with data found")
        return None

    except Exception:
        logger.exception("Failed to find latest export")
        return None


def list_parquet_files(s3_client: Any, bucket: str, prefix: str) -> list[str]:
    """List all parquet files in an S3 prefix."""
    parquet_files = []
    paginator = s3_client.get_paginator("list_objects_v2")

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".parquet"):
                parquet_files.append(f"s3://{bucket}/{key}")
                if len(parquet_files) <= 3:
                    logger.info("  File: s3://%s/%s", bucket, key)

    return parquet_files


# --- Spark Processing ---


def read_traces(spark: SparkSession, parquet_files: list[str]) -> Any:
    """Read traces from Parquet files.

    Returns DataFrame with columns needed for label validation analysis.
    """
    logger.info("Reading %d parquet files...", len(parquet_files))

    needed_columns = [
        "id",
        "trace_id",
        "parent_run_id",
        "name",
        "run_type",
        "status",
        "start_time",
        "end_time",
        "extra",
        "inputs",
        "outputs",
    ]

    df = spark.read.parquet(*parquet_files).select(*needed_columns)

    # Convert timestamps if needed
    if isinstance(df.schema["start_time"].dataType, LongType):
        df = df.withColumn(
            "start_time",
            (F.col("start_time") / 1_000_000_000).cast("timestamp"),
        ).withColumn(
            "end_time",
            (F.col("end_time") / 1_000_000_000).cast("timestamp"),
        )

    logger.info("Read %d traces", df.count())
    return df


def extract_receipt_traces(df: Any) -> list[dict[str, Any]]:
    """Extract receipt_processing root traces with their validation data.

    Returns list of dicts with:
        - image_id, receipt_id
        - trace_id (for looking up children)
        - outputs (validation results)
        - duration_ms
    """
    # Get root receipt_processing traces
    roots = df.filter(F.col("name") == "receipt_processing")

    # Extract metadata from extra field
    roots = (
        roots.withColumn(
            "image_id",
            F.get_json_object(F.col("extra"), "$.metadata.image_id"),
        )
        .withColumn(
            "receipt_id",
            F.get_json_object(F.col("extra"), "$.metadata.receipt_id").cast("int"),
        )
        .withColumn(
            "duration_ms",
            (F.col("end_time").cast("double") - F.col("start_time").cast("double"))
            * 1000,
        )
    )

    # Collect root traces
    root_data = roots.select(
        "trace_id", "image_id", "receipt_id", "outputs", "duration_ms"
    ).collect()

    logger.info("Found %d receipt_processing root traces", len(root_data))
    return [row.asDict() for row in root_data]


def extract_validation_traces(df: Any, trace_ids: list[str]) -> dict[str, list[dict]]:
    """Extract label_validation_chroma and label_validation_llm traces.

    Returns dict mapping trace_id -> list of validation dicts.
    """
    # Filter to validation traces in our trace IDs
    validations = df.filter(
        (F.col("trace_id").isin(trace_ids))
        & (
            F.col("name").isin(
                ["label_validation_chroma", "label_validation_llm", "llm_batch_validation"]
            )
        )
    )

    # Extract validation data
    validations = validations.withColumn(
        "duration_ms",
        (F.col("end_time").cast("double") - F.col("start_time").cast("double")) * 1000,
    )

    validation_data = validations.select(
        "trace_id", "name", "outputs", "duration_ms"
    ).collect()

    # Group by trace_id
    result: dict[str, list[dict]] = {}
    for row in validation_data:
        trace_id = row["trace_id"]
        if trace_id not in result:
            result[trace_id] = []

        outputs = row["outputs"]
        if isinstance(outputs, str):
            try:
                outputs = json.loads(outputs)
            except json.JSONDecodeError:
                outputs = {}

        result[trace_id].append(
            {
                "name": row["name"],
                "outputs": outputs or {},
                "duration_ms": row["duration_ms"],
            }
        )

    logger.info("Extracted validation traces for %d receipts", len(result))
    return result


# --- Visualization Building ---


def build_viz_receipt(
    root_trace: dict[str, Any],
    validation_traces: list[dict],
    receipt_lookup: dict[tuple[str, int], dict[str, Any]],
) -> dict[str, Any] | None:
    """Build a single visualization receipt.

    Combines:
    - Root trace metadata (image_id, receipt_id, duration)
    - Validation traces (chroma/llm decisions per word)
    - Receipt lookup (CDN keys, word bboxes, labels from DynamoDB)
    """
    image_id = root_trace.get("image_id")
    receipt_id = root_trace.get("receipt_id")

    if not image_id or receipt_id is None:
        return None

    # Get receipt data from lookup
    receipt_data = receipt_lookup.get((image_id, receipt_id))
    if not receipt_data:
        logger.debug("No receipt data for %s_%d", image_id, receipt_id)
        return None

    # Get words and labels from receipt lookup
    words_data = receipt_data.get("words", [])
    labels_data = receipt_data.get("labels", {})

    if not words_data:
        logger.debug("No words for %s_%d", image_id, receipt_id)
        return None

    # Parse root trace outputs
    root_outputs = root_trace.get("outputs")
    if isinstance(root_outputs, str):
        try:
            root_outputs = json.loads(root_outputs)
        except json.JSONDecodeError:
            root_outputs = {}
    root_outputs = root_outputs or {}

    # Extract validation decisions from child traces
    chroma_validations = []
    llm_validations = []
    chroma_duration_ms = 0.0
    llm_duration_ms = 0.0

    for trace in validation_traces:
        name = trace.get("name", "")
        outputs = trace.get("outputs", {})
        duration = trace.get("duration_ms", 0.0)

        if name == "label_validation_chroma":
            chroma_duration_ms += duration
            # Handle both single validation and batch outputs
            if "validations" in outputs:
                chroma_validations.extend(outputs["validations"])
            elif "line_id" in outputs:
                chroma_validations.append(outputs)

        elif name in ("label_validation_llm", "llm_batch_validation"):
            llm_duration_ms += duration
            if "validations" in outputs:
                llm_validations.extend(outputs["validations"])
            elif "line_id" in outputs:
                llm_validations.append(outputs)

    # Build validation lookup: (line_id, word_id) -> validation info
    # Does NOT default to VALID - uses null for missing trace data
    validation_lookup: dict[tuple[int, int], dict] = {}

    for v in chroma_validations:
        key = (v.get("line_id", 0), v.get("word_id", 0))
        decision = v.get("decision", "").upper()
        # Normalize CORRECT -> CORRECTED
        if decision == "CORRECT":
            decision = "CORRECTED"
        validation_lookup[key] = {
            "validation_source": "chroma",
            "decision": decision if decision else None,
            "confidence": v.get("confidence"),
        }

    for v in llm_validations:
        key = (v.get("line_id", 0), v.get("word_id", 0))
        decision = v.get("decision", "").upper()
        # Normalize CORRECT -> CORRECTED
        if decision == "CORRECT":
            decision = "CORRECTED"
        validation_lookup[key] = {
            "validation_source": "llm",
            "decision": decision if decision else None,
            "confidence": v.get("confidence"),
        }

    # Build visualization words
    viz_words = []
    for word in words_data:
        line_id = word.get("line_id")
        word_id = word.get("word_id")
        key = (line_id, word_id)

        # Get label from lookup (string key format)
        # labels_data can be either:
        # - Simple: {f"{line_id}_{word_id}": "LABEL"}
        # - Extended: {f"{line_id}_{word_id}": {"label": "LABEL", "validation_status": "VALID"}}
        label_entry = labels_data.get(f"{line_id}_{word_id}", "")
        if isinstance(label_entry, dict):
            label = label_entry.get("label", "")
            validation_status = label_entry.get("validation_status", "PENDING")
        else:
            label = label_entry
            validation_status = "PENDING"  # Default for simple format

        # Only include words with labels (validated words)
        if label:
            validation = validation_lookup.get(key)
            viz_words.append(
                {
                    "text": word.get("text", ""),
                    "line_id": line_id,
                    "word_id": word_id,
                    "bbox": word.get("bbox", {}),
                    "label": label,
                    # Include DynamoDB validation_status - this is the source of truth
                    "validation_status": validation_status,
                    # Trace info - may be null if no trace found
                    "validation_source": validation.get("validation_source")
                    if validation
                    else None,
                    "decision": validation.get("decision") if validation else None,
                }
            )

    if not viz_words:
        logger.debug("No validated words for %s_%d", image_id, receipt_id)
        return None

    # Build tier summaries
    # Does NOT default to VALID - only counts words with actual decisions
    chroma_decisions = {"VALID": 0, "CORRECTED": 0, "NEEDS_REVIEW": 0}
    llm_decisions = {"VALID": 0, "CORRECTED": 0, "NEEDS_REVIEW": 0}

    for word in viz_words:
        decision = word.get("decision")
        source = word.get("validation_source")

        # Skip words without trace validation data
        if decision is None or source is None:
            continue

        # Normalize decision
        if decision == "VALID":
            norm_decision = "VALID"
        elif decision in ("CORRECTED", "CORRECT"):
            norm_decision = "CORRECTED"
        elif decision in ("NEEDS_REVIEW", "NEEDS REVIEW"):
            norm_decision = "NEEDS_REVIEW"
        else:
            # Unknown decision - don't count
            continue

        if source == "chroma":
            chroma_decisions[norm_decision] += 1
        else:
            llm_decisions[norm_decision] += 1

    chroma_count = sum(chroma_decisions.values())
    llm_count = sum(llm_decisions.values())

    chroma_tier = {
        "tier": "chroma",
        "words_count": chroma_count,
        "duration_seconds": chroma_duration_ms / 1000,
        "decisions": chroma_decisions,
    }

    llm_tier = None
    if llm_count > 0:
        llm_tier = {
            "tier": "llm",
            "words_count": llm_count,
            "duration_seconds": llm_duration_ms / 1000,
            "decisions": llm_decisions,
        }

    # Get CDN keys
    cdn_s3_key = receipt_data.get("cdn_s3_key", "")
    if not cdn_s3_key:
        return None

    # Build step timings from what's available in traces
    step_timings = {}
    if chroma_duration_ms > 0:
        step_timings["chroma_validation"] = {
            "duration_ms": chroma_duration_ms,
            "duration_seconds": chroma_duration_ms / 1000,
        }
    if llm_duration_ms > 0:
        step_timings["llm_validation"] = {
            "duration_ms": llm_duration_ms,
            "duration_seconds": llm_duration_ms / 1000,
        }
    # Total processing time from root trace
    total_duration_ms = root_trace.get("duration_ms", 0)
    if total_duration_ms and total_duration_ms > 0:
        step_timings["total"] = {
            "duration_ms": total_duration_ms,
            "duration_seconds": total_duration_ms / 1000,
        }

    return {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "merchant_name": root_outputs.get("merchant_name"),
        "words": viz_words,
        "chroma": chroma_tier,
        "llm": llm_tier,
        # Step timing breakdown
        "step_timings": step_timings,
        "cdn_s3_key": cdn_s3_key,
        "cdn_webp_s3_key": receipt_data.get("cdn_webp_s3_key"),
        "cdn_avif_s3_key": receipt_data.get("cdn_avif_s3_key"),
        "cdn_medium_s3_key": receipt_data.get("cdn_medium_s3_key"),
        "cdn_medium_webp_s3_key": receipt_data.get("cdn_medium_webp_s3_key"),
        "cdn_medium_avif_s3_key": receipt_data.get("cdn_medium_avif_s3_key"),
        "width": receipt_data.get("width", 0),
        "height": receipt_data.get("height", 0),
    }


def calculate_aggregate_stats(receipts: list[dict]) -> dict[str, Any]:
    """Calculate aggregate statistics from receipts."""
    if not receipts:
        return {"total_receipts": 0, "avg_chroma_rate": 0.0}

    total_words = 0
    chroma_words = 0
    total_valid = 0
    total_corrected = 0
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
        total_corrected += chroma_decisions.get("CORRECTED", 0)
        total_needs_review += chroma_decisions.get("NEEDS_REVIEW", 0)

        if llm:
            llm_decisions = llm.get("decisions", {})
            total_valid += llm_decisions.get("VALID", 0)
            total_corrected += llm_decisions.get("CORRECTED", 0)
            total_needs_review += llm_decisions.get("NEEDS_REVIEW", 0)

    avg_chroma_rate = (chroma_words / total_words * 100) if total_words > 0 else 0.0

    return {
        "total_receipts": len(receipts),
        "avg_chroma_rate": round(avg_chroma_rate, 1),
        "total_valid": total_valid,
        "total_corrected": total_corrected,
        "total_needs_review": total_needs_review,
    }


# --- Cache Writing ---


def write_cache(
    s3_client: Any,
    bucket: str,
    receipts: list[dict[str, Any]],
    parquet_prefix: str,
) -> None:
    """Write individual receipt files + metadata to S3."""
    timestamp = datetime.now(timezone.utc)
    cache_version = timestamp.strftime("%Y%m%d-%H%M%S")
    receipts_prefix = "receipts/"

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

    # Parallel upload
    uploaded_keys = []
    failed_count = 0
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_receipt = {executor.submit(upload_receipt, r): r for r in receipts}
        for future in as_completed(future_to_receipt):
            try:
                key = future.result()
                uploaded_keys.append(key)
                if len(uploaded_keys) % 20 == 0:
                    logger.info("Uploaded %d/%d receipts", len(uploaded_keys), len(receipts))
            except Exception:
                failed_count += 1
                logger.exception("Failed to upload receipt")

    if failed_count > 0:
        logger.warning("Completed with %d failures", failed_count)

    # Write metadata.json
    aggregate_stats = calculate_aggregate_stats(receipts)
    metadata = {
        "version": cache_version,
        "parquet_prefix": parquet_prefix,
        "receipt_keys": uploaded_keys,
        "aggregate_stats": aggregate_stats,
        "cached_at": timestamp.isoformat(),
    }
    logger.info("Writing metadata.json")
    s3_client.put_object(
        Bucket=bucket,
        Key="metadata.json",
        Body=json.dumps(metadata, indent=2),
        ContentType="application/json",
    )

    # Write latest.json pointer
    latest_pointer = {
        "version": cache_version,
        "receipts_prefix": receipts_prefix,
        "metadata_key": "metadata.json",
        "updated_at": timestamp.isoformat(),
    }
    s3_client.put_object(
        Bucket=bucket,
        Key="latest.json",
        Body=json.dumps(latest_pointer, indent=2),
        ContentType="application/json",
    )

    logger.info("Cache generation complete!")
    logger.info("  Version: %s", cache_version)
    logger.info("  Total receipts: %d", len(receipts))
    logger.info("  Avg ChromaDB rate: %.1f%%", aggregate_stats["avg_chroma_rate"])


# --- Main ---


def main() -> int:
    """Main entry point."""
    args = parse_args()

    logger.info("Starting Label Validation visualization cache generation")
    logger.info("Parquet bucket: s3://%s", args.parquet_bucket)
    logger.info("Cache bucket: s3://%s", args.cache_bucket)
    logger.info("Receipts JSON: %s", args.receipts_json)
    logger.info("Max receipts: %d", args.max_receipts)

    s3_client = boto3.client("s3")

    # Resolve parquet prefix
    parquet_prefix = args.parquet_prefix
    if parquet_prefix == "traces/" or not parquet_prefix.startswith("traces/export_id="):
        detected = find_latest_export_prefix(s3_client, args.parquet_bucket)
        if detected:
            parquet_prefix = detected
        else:
            logger.error("Could not find any export with data")
            return 1

    logger.info("Using parquet prefix: %s", parquet_prefix)

    # List parquet files
    parquet_files = list_parquet_files(s3_client, args.parquet_bucket, parquet_prefix)
    if not parquet_files:
        logger.error("No parquet files found")
        return 1

    # Load receipt lookup
    receipt_lookup = load_receipts_from_s3(s3_client, args.receipts_json)

    # Initialize Spark
    logger.info("Initializing Spark...")
    spark = SparkSession.builder.appName("LabelValidationVizCache").getOrCreate()

    try:
        # Read traces
        df = read_traces(spark, parquet_files)

        # Extract root receipt traces
        root_traces = extract_receipt_traces(df)
        if not root_traces:
            logger.error("No receipt_processing traces found")
            return 1

        # Extract validation traces
        trace_ids = [t["trace_id"] for t in root_traces if t.get("trace_id")]
        validation_traces = extract_validation_traces(df, trace_ids)

        # Build visualization receipts
        viz_receipts = []
        for root in root_traces:
            trace_id = root.get("trace_id")
            validations = validation_traces.get(trace_id, [])

            receipt = build_viz_receipt(root, validations, receipt_lookup)
            if receipt:
                viz_receipts.append(receipt)

            if len(viz_receipts) >= args.max_receipts:
                break

        if not viz_receipts:
            logger.error("No visualization receipts could be built")
            return 1

        logger.info("Built %d visualization receipts", len(viz_receipts))

        # Write cache
        write_cache(s3_client, args.cache_bucket, viz_receipts, parquet_prefix)

        return 0

    except Exception:
        logger.exception("Cache generation failed")
        return 1

    finally:
        spark.stop()


if __name__ == "__main__":
    sys.exit(main())
