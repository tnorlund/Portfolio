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
        --execution-id abc123 \\
        --receipts-lookup s3://label-evaluator-batch-bucket/receipts_lookup/abc123/

Key Differences from viz_cache_job.py:
    - Reads words/labels from S3 JSON files (data/{execution_id}/*.json)
    - Reads evaluation results from S3 JSON (unified/{execution_id}/*.json)
    - Reads CDN keys from receipts_lookup JSONL dataset
    - No longer depends on LangSmith Parquet exports for viz-cache
    - Analytics still uses Parquet for full trace analysis
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

import boto3
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
from pyspark.storagelevel import StorageLevel

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
        help="(Legacy) S3 path to receipts-lookup.json (optional for viz-cache)",
    )
    parser.add_argument(
        "--receipts-lookup",
        help="S3 path/prefix to receipts-lookup JSONL dataset (optional)",
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


def run_analytics(spark: SparkSession, args: argparse.Namespace) -> None:
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


def to_s3a(path: str) -> str:
    """Convert s3:// URIs to s3a:// for Spark."""
    if path.startswith("s3://"):
        return f"s3a://{path[5:]}"
    return path


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
        multi_line: If True, each file can contain a single multi-line JSON object.
                    If False, expects JSONL format (one JSON per line).
    """
    spark_path = to_s3a(path)
    reader = spark.read.option("multiLine", str(multi_line).lower())
    if schema is not None:
        reader = reader.schema(schema)
    return reader.json(spark_path)


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
        if not legacy_receipts_json.startswith("s3://"):
            raise ValueError(f"Invalid S3 path: {legacy_receipts_json}")
        s3_client = boto3.client("s3")
        path = legacy_receipts_json[5:]
        bucket, key = path.split("/", 1)
        response = s3_client.get_object(Bucket=bucket, Key=key)
        raw_lookup = json.loads(response["Body"].read().decode("utf-8"))

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
    total += result.get("geometric_issues_found", 0)

    # Decision-based issues (INVALID + NEEDS_REVIEW)
    for key in (
        "currency_decisions",
        "metadata_decisions",
        "financial_decisions",
    ):
        decisions = result.get(key, {})
        total += decisions.get("INVALID", 0)
        total += decisions.get("NEEDS_REVIEW", 0)

    return total


def build_viz_receipt_from_row(
    row: dict[str, Any], execution_id: str
) -> dict[str, Any] | None:
    """Build a single viz-cache receipt entry from a Spark row."""
    image_id = row.get("image_id")
    receipt_id = row.get("receipt_id")

    if not image_id or receipt_id is None:
        return None

    words = row.get("words") or []
    labels = row.get("labels") or []

    # Build words with labels
    words_with_labels = build_words_with_labels(words, labels)

    # CDN keys (from lookup)
    cdn_s3_key = row.get("cdn_s3_key")
    if cdn_s3_key:
        cdn_keys = generate_cdn_keys(cdn_s3_key)
        for key in cdn_keys:
            if row.get(key):
                cdn_keys[key] = row.get(key)
    else:
        cdn_keys = {
            "cdn_s3_key": None,
            "cdn_webp_s3_key": None,
            "cdn_avif_s3_key": None,
            "cdn_medium_s3_key": None,
            "cdn_medium_webp_s3_key": None,
            "cdn_medium_avif_s3_key": None,
        }

    width = row.get("width") or 800
    height = row.get("height") or 2400

    merchant_name = row.get("merchant_name") or "Unknown"
    issues_found = row.get("issues_found")
    if issues_found is None:
        issues_found = count_issues(row)

    # Currency decisions
    currency_decisions = row.get("currency_decisions", {})
    currency = {
        "decisions": {
            "VALID": currency_decisions.get("VALID", 0),
            "INVALID": currency_decisions.get("INVALID", 0),
            "NEEDS_REVIEW": currency_decisions.get("NEEDS_REVIEW", 0),
        },
        "all_decisions": row.get("currency_all_decisions", []),
        "duration_seconds": row.get("currency_duration_seconds", 0),
    }

    # Metadata decisions
    metadata_decisions = row.get("metadata_decisions", {})
    metadata = {
        "decisions": {
            "VALID": metadata_decisions.get("VALID", 0),
            "INVALID": metadata_decisions.get("INVALID", 0),
            "NEEDS_REVIEW": metadata_decisions.get("NEEDS_REVIEW", 0),
        },
        "all_decisions": row.get("metadata_all_decisions", []),
        "duration_seconds": row.get("metadata_duration_seconds", 0),
    }

    # Financial decisions
    financial_decisions = row.get("financial_decisions", {})
    financial = {
        "decisions": {
            "VALID": financial_decisions.get("VALID", 0),
            "INVALID": financial_decisions.get("INVALID", 0),
            "NEEDS_REVIEW": financial_decisions.get("NEEDS_REVIEW", 0),
        },
        "all_decisions": row.get("financial_all_decisions", []),
        "duration_seconds": row.get("financial_duration_seconds", 0),
    }

    # Geometric issues
    geometric = {
        "issues_found": row.get("geometric_issues_found", 0),
        "issues": row.get("geometric_issues", []),
        "duration_seconds": row.get("geometric_duration_seconds", 0),
    }

    review = None
    if row.get("review_all_decisions") or row.get("review_decisions"):
        review_decisions = row.get("review_decisions", {})
        review = {
            "decisions": {
                "VALID": review_decisions.get("VALID", 0),
                "INVALID": review_decisions.get("INVALID", 0),
                "NEEDS_REVIEW": review_decisions.get("NEEDS_REVIEW", 0),
            },
            "all_decisions": row.get("review_all_decisions", []),
            "duration_seconds": row.get("review_duration_seconds", 0),
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
        "review": review,
        **cdn_keys,
        "width": width,
        "height": height,
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
                s3_client.put_object(
                    Bucket=bucket,
                    Key=key,
                    Body=json.dumps(receipt, default=str),
                    ContentType="application/json",
                )
            except Exception as exc:
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
    s3_client.put_object(
        Bucket=bucket,
        Key="metadata.json",
        Body=json.dumps(metadata, indent=2),
        ContentType="application/json",
    )

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
