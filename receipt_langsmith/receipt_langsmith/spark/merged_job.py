#!/usr/bin/env python3
"""EMR Serverless job entry point for analytics and viz-cache generation.

This unified job handles both analytics (from LangSmith Parquet exports) and
viz-cache (from S3 JSON files), eliminating the need for separate job scripts.

Job Types:
    analytics: Run only analytics (computes job/receipt/step metrics)
    viz-cache: Run only viz-cache generation (reads from S3 JSON files)
    evaluator-viz-cache: Run evaluator viz-cache (from Parquet traces)
    all: Run analytics, viz-cache, and evaluator viz-cache

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
        --execution-id abc123 \\
        --receipts-lookup s3://label-evaluator-batch-bucket/ \\
            receipts_lookup/abc123/

Data Sources:
    - Analytics: LangSmith Parquet exports (full trace analysis)
    - Viz-cache: S3 JSON files (data/{execution_id}/*.json,
      unified/{execution_id}/*.json)
    - CDN keys: receipts_lookup JSONL dataset
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from py4j.protocol import Py4JError, Py4JJavaError
from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import (
    ArrayType,
    DoubleType,
    IntegerType,
    StringType,
    StructField,
    StructType,
)
from pyspark.sql.utils import AnalysisException
from pyspark.storagelevel import StorageLevel

from receipt_langsmith.spark.processor import LangSmithSparkProcessor
from receipt_langsmith.spark.cli import configure_logging
from receipt_langsmith.spark.s3_io import (
    load_json_from_s3,
    ReceiptsCachePointer,
    write_receipt_cache_index,
    write_receipt_json,
)
from receipt_langsmith.spark.utils import to_s3a

configure_logging()
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Merged analytics and viz-cache job for EMR Serverless"
    )

    parser.add_argument(
        "--job-type",
        choices=["analytics", "viz-cache", "qa-cache", "evaluator-viz-cache", "all"],
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
        help=(
            "S3 bucket with receipt data and results (required for "
            "viz-cache)"
        ),
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
        help=(
            "(Legacy) S3 path to receipts-lookup.json "
            "(optional for viz-cache)"
        ),
    )
    parser.add_argument(
        "--receipts-lookup",
        help="S3 path/prefix to receipts-lookup JSONL dataset (optional)",
    )

    # QA cache arguments
    parser.add_argument(
        "--results-ndjson",
        help="S3 path to question-results.ndjson (required for qa-cache)",
    )
    parser.add_argument(
        "--langchain-project",
        default="",
        help="LangSmith project name (for qa-cache metadata)",
    )

    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    """Validate required arguments based on job type."""
    requirements = {
        "analytics": {
            "parquet_input": "--parquet-input required for analytics",
            "analytics_output": "--analytics-output required for analytics",
        },
        "viz-cache": {
            "batch_bucket": "--batch-bucket required for viz-cache",
            "cache_bucket": "--cache-bucket required for viz-cache",
            "execution_id": "--execution-id required for viz-cache",
        },
        "qa-cache": {
            "parquet_input": "--parquet-input required for qa-cache",
            "cache_bucket": "--cache-bucket required for qa-cache",
            "results_ndjson": "--results-ndjson required for qa-cache",
            "receipts_json": "--receipts-json required for qa-cache",
            "execution_id": "--execution-id required for qa-cache",
        },
        "evaluator-viz-cache": {
            "parquet_input": "--parquet-input required for evaluator-viz-cache",
            "cache_bucket": "--cache-bucket required for evaluator-viz-cache",
            "execution_id": "--execution-id required for evaluator-viz-cache",
        },
    }

    job_types = (
        ("analytics", "viz-cache")
        if args.job_type == "all"
        else (args.job_type,)
    )

    for job_type in job_types:
        for field, message in requirements.get(job_type, {}).items():
            if not getattr(args, field, None):
                raise ValueError(message)


# =============================================================================
# Analytics Functions
# =============================================================================


def run_analytics(spark: SparkSession, args: argparse.Namespace) -> None:
    """Run analytics job using LangSmithSparkProcessor."""
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
        partition_by = (
            ["merchant_name"] if args.partition_by_merchant else None
        )
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
# Viz-Cache Functions (Spark-based)
# =============================================================================


DATA_SCHEMA = StructType(
    [
        StructField("image_id", StringType(), True),
        StructField("receipt_id", IntegerType(), True),
        StructField("merchant_name", StringType(), True),
        StructField(
            "words",
            ArrayType(
                StructType(
                    [
                        StructField("text", StringType(), True),
                        StructField("line_id", IntegerType(), True),
                        StructField("word_id", IntegerType(), True),
                        StructField(
                            "bounding_box",
                            StructType(
                                [
                                    StructField("x", DoubleType(), True),
                                    StructField("y", DoubleType(), True),
                                    StructField("width", DoubleType(), True),
                                    StructField("height", DoubleType(), True),
                                ]
                            ),
                            True,
                        ),
                    ]
                )
            ),
            True,
        ),
        StructField(
            "labels",
            ArrayType(
                StructType(
                    [
                        StructField("line_id", IntegerType(), True),
                        StructField("word_id", IntegerType(), True),
                        StructField("label", StringType(), True),
                    ]
                )
            ),
            True,
        ),
    ]
)

LOOKUP_SCHEMA = StructType(
    [
        StructField("image_id", StringType(), True),
        StructField("receipt_id", IntegerType(), True),
        StructField("cdn_s3_key", StringType(), True),
        StructField("cdn_webp_s3_key", StringType(), True),
        StructField("cdn_avif_s3_key", StringType(), True),
        StructField("cdn_medium_s3_key", StringType(), True),
        StructField("cdn_medium_webp_s3_key", StringType(), True),
        StructField("cdn_medium_avif_s3_key", StringType(), True),
        StructField("width", IntegerType(), True),
        StructField("height", IntegerType(), True),
    ]
)


def read_json_df(
    spark: SparkSession,
    path: str,
    schema: StructType | None = None,
    multi_line: bool = True,
) -> Any:
    """Read JSON from S3 (prefix or file) into a DataFrame.

    Args:
        spark: SparkSession
        path: S3 path (s3:// or s3a://)
        schema: Optional schema to apply
        multi_line: If True, each file can contain a single multi-line JSON
            object. If False, expects JSONL format (one JSON per line).
    """
    spark_path = to_s3a(path)
    reader = spark.read.option("multiLine", str(multi_line).lower())
    if schema is not None:
        reader = reader.schema(schema)
    return reader.json(spark_path)


def read_legacy_lookup_json(legacy_receipts_json: str) -> dict[str, Any]:
    """Read legacy receipts lookup JSON from S3."""
    s3_client = boto3.client("s3")
    payload = load_json_from_s3(s3_client, legacy_receipts_json)
    if not isinstance(payload, dict):
        raise ValueError("Legacy receipts lookup must be a JSON object")
    return payload


def normalize_legacy_lookup_rows(
    raw_lookup: dict[str, Any],
) -> list[dict[str, Any]]:
    """Normalize legacy lookup JSON into rows for DataFrame creation."""
    rows = []
    for composite_key, receipt_data in raw_lookup.items():
        parts = composite_key.rsplit("_", 1)
        if len(parts) != 2:
            continue
        image_id, receipt_id_str = parts
        try:
            receipt_id = int(receipt_id_str)
        except ValueError:
            continue

        if isinstance(receipt_data, str):
            receipt_row = {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "cdn_s3_key": receipt_data,
                "cdn_webp_s3_key": None,
                "cdn_avif_s3_key": None,
                "cdn_medium_s3_key": None,
                "cdn_medium_webp_s3_key": None,
                "cdn_medium_avif_s3_key": None,
                "width": 800,
                "height": 2400,
            }
        else:
            receipt_row = {
                "image_id": image_id,
                "receipt_id": receipt_id,
                **receipt_data,
            }
        rows.append(receipt_row)
    return rows


def load_receipts_lookup_df(
    spark: SparkSession,
    receipts_lookup_path: str | None,
    legacy_receipts_json: str | None,
) -> Any | None:
    """Load receipt lookup dataset for CDN keys."""
    if receipts_lookup_path:
        logger.info(
            "Reading receipts lookup dataset from %s", receipts_lookup_path
        )
        return read_json_df(spark, receipts_lookup_path, schema=LOOKUP_SCHEMA)

    if legacy_receipts_json:
        logger.info(
            "Reading legacy receipts lookup from %s", legacy_receipts_json
        )
        raw_lookup = read_legacy_lookup_json(legacy_receipts_json)
        rows = normalize_legacy_lookup_rows(raw_lookup)
        if not rows:
            return None
        return spark.createDataFrame(rows, schema=LOOKUP_SCHEMA)

    return None


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
        if (
            line_id is not None
            and word_id is not None
            and label_text is not None
        ):
            label_lookup[(int(line_id), int(word_id))] = str(label_text)

    result = []
    for word in words:
        line_id = word.get("line_id")
        word_id = word.get("word_id")
        bbox = word.get("bounding_box", {})

        # Look up label if both IDs are present
        word_label: str | None = None
        if line_id is not None and word_id is not None:
            word_label = label_lookup.get((int(line_id), int(word_id)))

        word_with_label = {
            "text": word.get("text", ""),
            "label": word_label,
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
    total += int(result.get("geometric_issues_found", 0) or 0)

    # Decision-based issues (INVALID + NEEDS_REVIEW)
    for key in (
        "currency_decisions",
        "metadata_decisions",
        "financial_decisions",
    ):
        decisions = result.get(key, {}) or {}
        if isinstance(decisions, dict):
            total += int(decisions.get("INVALID", 0) or 0)
            total += int(decisions.get("NEEDS_REVIEW", 0) or 0)

    return total


def build_cdn_keys_from_row(row: dict[str, Any]) -> dict[str, Any]:
    """Build CDN keys from a unified row, honoring overrides."""
    cdn_s3_key = row.get("cdn_s3_key")
    if cdn_s3_key:
        cdn_keys = generate_cdn_keys(cdn_s3_key)
        for key in cdn_keys:
            if row.get(key):
                cdn_keys[key] = row.get(key)
        return cdn_keys
    return {
        "cdn_s3_key": None,
        "cdn_webp_s3_key": None,
        "cdn_avif_s3_key": None,
        "cdn_medium_s3_key": None,
        "cdn_medium_webp_s3_key": None,
        "cdn_medium_avif_s3_key": None,
    }


def build_decisions_block(row: dict[str, Any], prefix: str) -> dict[str, Any]:
    """Build a decisions block for a given prefix (currency/metadata/etc.)."""
    decisions = row.get(f"{prefix}_decisions", {})
    return {
        "decisions": {
            "VALID": decisions.get("VALID", 0),
            "INVALID": decisions.get("INVALID", 0),
            "NEEDS_REVIEW": decisions.get("NEEDS_REVIEW", 0),
        },
        "all_decisions": row.get(f"{prefix}_all_decisions", []),
        "duration_seconds": row.get(f"{prefix}_duration_seconds", 0),
    }


def build_geometric_block(row: dict[str, Any]) -> dict[str, Any]:
    """Build the geometric issues block."""
    return {
        "issues_found": row.get("geometric_issues_found", 0),
        "issues": row.get("geometric_issues", []),
        "duration_seconds": row.get("geometric_duration_seconds", 0),
    }


def build_review_block(row: dict[str, Any]) -> dict[str, Any] | None:
    """Build the review block only if review actually ran."""
    review_all_decisions = row.get("review_all_decisions") or []
    review_duration = row.get("review_duration_seconds") or 0
    if review_all_decisions or review_duration > 0:
        return build_decisions_block(row, "review")
    return None


def build_viz_receipt_from_row(
    row: dict[str, Any], execution_id: str
) -> dict[str, Any] | None:
    """Build a single viz-cache receipt entry from a Spark row."""
    image_id = row.get("image_id")
    receipt_id = row.get("receipt_id")

    if not image_id or receipt_id is None:
        return None

    issues_found = row.get("issues_found")
    if issues_found is None:
        issues_found = count_issues(row)

    return {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "merchant_name": row.get("merchant_name") or "Unknown",
        "execution_id": execution_id,
        "issues_found": issues_found,
        "words": build_words_with_labels(
            row.get("words") or [],
            row.get("labels") or [],
        ),
        "geometric": build_geometric_block(row),
        "currency": build_decisions_block(row, "currency"),
        "metadata": build_decisions_block(row, "metadata"),
        "financial": build_decisions_block(row, "financial"),
        "review": build_review_block(row),
        **build_cdn_keys_from_row(row),
        "width": row.get("width") or 800,
        "height": row.get("height") or 2400,
    }


def write_viz_cache_parallel(df: Any, bucket: str, execution_id: str) -> None:
    """Write viz-cache files to S3 in parallel via Spark executors."""
    receipts_prefix = "receipts/"

    def upload_partition(rows):
        s3_client = boto3.client("s3")
        for row in rows:
            receipt = build_viz_receipt_from_row(
                row.asDict(recursive=True), execution_id
            )
            if not receipt:
                continue
            key = (
                f"{receipts_prefix}receipt-{receipt['image_id']}-"
                f"{receipt['receipt_id']}.json"
            )
            try:
                write_receipt_json(s3_client, bucket, key, receipt)
            except (ClientError, BotoCoreError, TypeError, ValueError) as exc:
                logger.warning(
                    "Failed to upload receipt %s_%s: %s",
                    receipt.get("image_id"),
                    receipt.get("receipt_id"),
                    exc,
                )

    df.foreachPartition(upload_partition)


def write_viz_cache_metadata(
    s3_client: Any,
    bucket: str,
    execution_id: str,
    total_receipts: int,
    receipts_with_issues: int,
) -> None:
    """Write metadata.json and latest.json to S3."""
    timestamp = datetime.now(timezone.utc)
    cache_version = timestamp.strftime("%Y%m%d-%H%M%S")
    receipts_prefix = "receipts/"

    metadata = {
        "version": cache_version,
        "execution_id": execution_id,
        "total_receipts": total_receipts,
        "receipts_with_issues": receipts_with_issues,
        "cached_at": timestamp.isoformat(),
    }
    pointer = ReceiptsCachePointer(
        cache_version, receipts_prefix, timestamp.isoformat()
    )
    write_receipt_cache_index(s3_client, bucket, metadata, pointer)

    logger.info(
        "Viz-cache metadata written. Version: %s, Receipts: %d",
        cache_version,
        total_receipts,
    )


def run_viz_cache(spark: SparkSession, args: argparse.Namespace) -> None:
    """Run viz-cache generation from S3 JSON files using Spark."""
    s3_client = boto3.client("s3")

    data_path = f"s3://{args.batch_bucket}/data/{args.execution_id}/"
    unified_path = f"s3://{args.batch_bucket}/unified/{args.execution_id}/"

    logger.info("Reading receipt data from %s", data_path)
    data_df = read_json_df(spark, data_path, schema=DATA_SCHEMA).select(
        "image_id",
        "receipt_id",
        "merchant_name",
        "words",
        "labels",
    )
    data_df = data_df.withColumnRenamed("merchant_name", "data_merchant_name")

    logger.info("Reading unified results from %s", unified_path)
    unified_df = read_json_df(spark, unified_path)

    # Ensure key fields exist
    if "merchant_name" not in unified_df.columns:
        unified_df = unified_df.withColumn("merchant_name", F.lit(None))

    def empty_decisions_struct():
        return F.struct(
            F.lit(0).cast("int").alias("VALID"),
            F.lit(0).cast("int").alias("INVALID"),
            F.lit(0).cast("int").alias("NEEDS_REVIEW"),
        )

    if "currency_decisions" not in unified_df.columns:
        unified_df = unified_df.withColumn(
            "currency_decisions", empty_decisions_struct()
        )
    if "metadata_decisions" not in unified_df.columns:
        unified_df = unified_df.withColumn(
            "metadata_decisions", empty_decisions_struct()
        )
    if "financial_decisions" not in unified_df.columns:
        unified_df = unified_df.withColumn(
            "financial_decisions", empty_decisions_struct()
        )
    if "geometric_issues_found" not in unified_df.columns:
        unified_df = unified_df.withColumn(
            "geometric_issues_found", F.lit(0).cast("int")
        )

    # Join receipt data with unified results
    joined = data_df.join(unified_df, ["image_id", "receipt_id"], "left")
    joined = joined.withColumn(
        "merchant_name",
        F.coalesce(F.col("merchant_name"), F.col("data_merchant_name")),
    ).drop("data_merchant_name")

    # Optional receipts lookup dataset for CDN keys
    lookup_df = load_receipts_lookup_df(
        spark, args.receipts_lookup, args.receipts_json
    )
    if lookup_df is not None:
        joined = joined.join(lookup_df, ["image_id", "receipt_id"], "left")
    else:
        logger.warning(
            "No receipts lookup provided; CDN keys may be missing in viz-cache"
        )

    def decision_issues(col_name: str) -> Any:
        return F.coalesce(F.col(f"{col_name}.INVALID"), F.lit(0)) + F.coalesce(
            F.col(f"{col_name}.NEEDS_REVIEW"), F.lit(0)
        )

    issues_expr = (
        F.coalesce(F.col("geometric_issues_found"), F.lit(0))
        + decision_issues("currency_decisions")
        + decision_issues("metadata_decisions")
        + decision_issues("financial_decisions")
    )
    joined = joined.withColumn("issues_found", issues_expr)

    joined.persist(StorageLevel.DISK_ONLY)

    total_receipts = joined.count()
    if total_receipts == 0:
        logger.error("No receipt data found in data/%s/", args.execution_id)
        joined.unpersist()
        return

    receipts_with_issues = joined.filter(F.col("issues_found") > 0).count()

    logger.info("Writing %d viz-cache receipts...", total_receipts)
    write_viz_cache_parallel(joined, args.cache_bucket, args.execution_id)

    write_viz_cache_metadata(
        s3_client,
        args.cache_bucket,
        args.execution_id,
        total_receipts,
        receipts_with_issues,
    )

    joined.unpersist()


# =============================================================================
# Evaluator Viz-Cache Functions
# =============================================================================


def _slugify(name: str) -> str:
    """Convert a merchant name to a filename-safe slug."""
    return name.lower().replace(" ", "-").replace("/", "-").replace("\\", "-")


def run_evaluator_viz_cache(
    parquet_dir: str, cache_bucket: str, execution_id: str
) -> None:
    """Run evaluator viz-cache helpers and write results to S3.

    Calls each of the 6 evaluator helpers independently. If one helper
    fails the remaining helpers still run.
    """
    # Import helpers at call-time so the module can be loaded even when the
    # helper packages are not installed (mirrors the qa-cache pattern).
    # pylint: disable=import-outside-toplevel
    from receipt_langsmith.spark.evaluator_financial_math_viz_cache import (
        build_financial_math_cache,
    )
    from receipt_langsmith.spark.evaluator_diff_viz_cache import (
        build_diff_cache,
    )
    from receipt_langsmith.spark.evaluator_journey_viz_cache import (
        build_journey_cache,
    )
    from receipt_langsmith.spark.evaluator_patterns_viz_cache import (
        build_patterns_cache,
    )
    from receipt_langsmith.spark.evaluator_evidence_viz_cache import (
        build_evidence_cache,
    )
    from receipt_langsmith.spark.evaluator_dedup_viz_cache import (
        build_dedup_cache,
    )
    # pylint: enable=import-outside-toplevel

    s3_client = boto3.client("s3")
    timestamp = datetime.now(timezone.utc).isoformat()

    helpers: list[tuple[str, Any, bool]] = [
        ("financial-math", build_financial_math_cache, False),
        ("diff", build_diff_cache, False),
        ("journey", build_journey_cache, False),
        ("patterns", build_patterns_cache, True),
        ("evidence", build_evidence_cache, False),
        ("dedup", build_dedup_cache, False),
    ]

    for prefix, helper_fn, is_merchant_keyed in helpers:
        try:
            logger.info("Running evaluator viz-cache helper: %s", prefix)
            results = helper_fn(parquet_dir)

            count = 0
            for item in results:
                if is_merchant_keyed:
                    slug = _slugify(item.get("merchant_name", "unknown"))
                    key = f"{prefix}/{slug}.json"
                else:
                    image_id = item.get("image_id", "unknown")
                    receipt_id = item.get("receipt_id", "unknown")
                    key = f"{prefix}/{image_id}_{receipt_id}.json"

                s3_client.put_object(
                    Bucket=cache_bucket,
                    Key=key,
                    Body=json.dumps(item, default=str).encode("utf-8"),
                    ContentType="application/json",
                )
                count += 1

            # Write metadata for this viz type
            metadata = {
                "count": count,
                "execution_id": execution_id,
                "cached_at": timestamp,
            }
            s3_client.put_object(
                Bucket=cache_bucket,
                Key=f"{prefix}/metadata.json",
                Body=json.dumps(metadata, default=str).encode("utf-8"),
                ContentType="application/json",
            )
            logger.info(
                "Evaluator viz-cache '%s' complete: %d items written",
                prefix,
                count,
            )

        except Exception:
            logger.exception(
                "Evaluator viz-cache helper '%s' failed; continuing with "
                "remaining helpers",
                prefix,
            )


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

    # Job entrypoint: ensure any unexpected failure logs clearly and exits
    # non-zero so EMR marks the step failed and surfaces the stack trace.
    try:
        spark = SparkSession.builder.appName(
            f"MergedJob-{args.job_type}"
        ).getOrCreate()

        # Run analytics if requested
        if args.job_type in ("analytics", "all"):
            logger.info("Starting analytics phase...")
            run_analytics(spark, args)
            logger.info("Analytics phase complete")

        # Run viz-cache if requested
        if args.job_type in ("viz-cache", "all"):
            logger.info("Starting viz-cache phase...")
            run_viz_cache(spark, args)
            logger.info("Viz-cache phase complete")

        # Run evaluator viz-cache if requested (standalone)
        if args.job_type == "evaluator-viz-cache":
            logger.info("Starting evaluator viz-cache phase...")
            run_evaluator_viz_cache(
                parquet_dir=args.parquet_input,
                cache_bucket=args.cache_bucket,
                execution_id=args.execution_id,
            )
            logger.info("Evaluator viz-cache phase complete")

        # Run evaluator viz-cache as part of "all" if parquet is available
        if args.job_type == "all" and getattr(args, "parquet_input", None):
            logger.info("Starting evaluator viz-cache phase...")
            run_evaluator_viz_cache(
                parquet_dir=args.parquet_input,
                cache_bucket=args.cache_bucket,
                execution_id=args.execution_id,
            )
            logger.info("Evaluator viz-cache phase complete")

        # Run QA cache if requested
        if args.job_type == "qa-cache":
            # pylint: disable=import-outside-toplevel
            from receipt_langsmith.spark.qa_viz_cache_job import (
                run_qa_cache_job,
            )
            from receipt_langsmith.spark.qa_viz_cache_helpers import (
                qa_cache_config_from_args,
            )
            # pylint: enable=import-outside-toplevel

            logger.info("Starting qa-cache phase...")
            config = qa_cache_config_from_args(
                args,
                default_max_questions=50,
            )
            run_qa_cache_job(spark, config=config)
            logger.info("QA cache phase complete")

        logger.info("All phases complete!")
        return 0

    except (
        Py4JJavaError,
        Py4JError,
        AnalysisException,
        BotoCoreError,
        ClientError,
    ):
        logger.exception("Job failed")
        return 1

    finally:
        if spark:
            spark.stop()


if __name__ == "__main__":
    sys.exit(main())
