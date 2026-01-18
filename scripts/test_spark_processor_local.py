#!/usr/bin/env python3
"""Local test script for LangSmith Spark processor.

This script mimics the EMR Serverless environment for local development.
Run from the portfolio_sagemaker directory:

    JAVA_HOME=/opt/homebrew/opt/openjdk@17 python scripts/test_spark_processor_local.py

Prerequisites:
    pip install pyspark pandas pyarrow
    brew install openjdk@17
"""

import os
import sys

# Add receipt_langsmith to path
sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "receipt_langsmith")
)

import logging

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def read_parquet_flexible(spark, path):
    """Read parquet with flexible schema - only select columns that exist."""
    logger.info(f"Reading Parquet from: {path}")

    # Read all columns first
    df = spark.read.parquet(path)

    # Log available columns
    logger.info(f"Available columns: {df.columns}")

    # Convert timestamps if needed
    from pyspark.sql.types import LongType

    if "start_time" in df.columns and isinstance(
        df.schema["start_time"].dataType, LongType
    ):
        df = df.withColumn(
            "start_time",
            (F.col("start_time") / 1_000_000_000).cast("timestamp"),
        ).withColumn(
            "end_time",
            (F.col("end_time") / 1_000_000_000).cast("timestamp"),
        )

    # Add missing columns with nulls if they don't exist
    if "trace_id" not in df.columns:
        # For root traces, trace_id = id. For children, we need to find root.
        # For now, use id as trace_id (will fix properly later)
        df = df.withColumn("trace_id", F.col("id"))

    if "total_tokens" not in df.columns:
        df = df.withColumn("total_tokens", F.lit(None).cast("long"))
    if "prompt_tokens" not in df.columns:
        df = df.withColumn("prompt_tokens", F.lit(None).cast("long"))
    if "completion_tokens" not in df.columns:
        df = df.withColumn("completion_tokens", F.lit(None).cast("long"))

    return df


def parse_json_fields_flexible(df):
    """Parse JSON fields and add computed columns."""
    # Add defensive check for extra column
    if "extra" not in df.columns:
        df = df.withColumn("extra", F.lit("{}"))
    return (
        df.withColumn(
            "metadata_execution_id",
            F.get_json_object(F.col("extra"), "$.metadata.execution_id"),
        )
        .withColumn(
            "metadata_merchant_name",
            F.get_json_object(F.col("extra"), "$.metadata.merchant_name"),
        )
        .withColumn(
            "metadata_image_id",
            F.get_json_object(F.col("extra"), "$.metadata.image_id"),
        )
        .withColumn(
            "metadata_receipt_id",
            F.get_json_object(F.col("extra"), "$.metadata.receipt_id").cast(
                "int"
            ),
        )
        .withColumn(
            "duration_ms",
            (
                F.col("end_time").cast("double")
                - F.col("start_time").cast("double")
            )
            * 1000,
        )
    )


def main():
    # Input path - clean data from merchant-trace-test-004
    input_path = "/tmp/langsmith_traces_test004/"
    output_path = "/tmp/langsmith_analytics_output/"

    logger.info("=" * 60)
    logger.info("LOCAL SPARK PROCESSOR TEST")
    logger.info("=" * 60)
    logger.info(f"Input: {input_path}")
    logger.info(f"Output: {output_path}")

    # Initialize Spark with local mode
    spark = (
        SparkSession.builder.appName("LangSmithAnalytics-LocalTest")
        .master("local[*]")
        .config("spark.sql.legacy.parquet.nanosAsLong", "true")
        .config("spark.driver.memory", "2g")
        .getOrCreate()
    )

    # Set log level to reduce noise
    spark.sparkContext.setLogLevel("WARN")

    try:
        # Read with flexible schema
        logger.info("Reading Parquet data...")
        df = read_parquet_flexible(spark, input_path)
        logger.info(f"Raw rows: {df.count()}")

        logger.info("Parsing JSON fields...")
        parsed = parse_json_fields_flexible(df)
        parsed.cache()

        # Show what we have
        logger.info("\n" + "=" * 60)
        logger.info("DATA OVERVIEW")
        logger.info("=" * 60)

        logger.info("\nTrace names:")
        parsed.groupBy("name").count().orderBy("count", ascending=False).show(
            30, truncate=False
        )

        logger.info("\nRoot traces (parent_run_id is null):")
        parsed.filter(parsed.parent_run_id.isNull()).select(
            "name", "id", "status", "duration_ms"
        ).show(truncate=False)

        # ============================================================
        # PHASE 4: JOB ANALYTICS
        # ============================================================
        logger.info("\n" + "=" * 60)
        logger.info("PHASE 4: JOB ANALYTICS IMPLEMENTATION")
        logger.info("=" * 60)

        # Identify job types by root trace name
        jobs = parsed.filter(
            (F.col("name") == "PatternComputation")
            | (F.col("name") == "ReceiptEvaluation")
        )

        # Add job_type column
        jobs_with_type = jobs.withColumn(
            "job_type",
            F.when(F.col("name") == "PatternComputation", "phase1_patterns")
            .when(F.col("name") == "ReceiptEvaluation", "phase2_evaluation")
            .otherwise("unknown"),
        )

        logger.info("\n1. Job type breakdown:")
        jobs_with_type.groupBy("job_type", "name").count().show()

        logger.info("\n2. Job analytics by type:")
        job_analytics = jobs_with_type.groupBy("job_type").agg(
            F.count("*").alias("job_count"),
            F.avg("duration_ms").alias("avg_duration_ms"),
            F.min("duration_ms").alias("min_duration_ms"),
            F.max("duration_ms").alias("max_duration_ms"),
            F.sum("total_tokens").alias("total_tokens"),
        )
        job_analytics.show(truncate=False)

        logger.info("\n3. Job analytics by type and merchant:")
        job_by_merchant = jobs_with_type.groupBy(
            "job_type", "metadata_merchant_name"
        ).agg(
            F.count("*").alias("job_count"),
            F.avg("duration_ms").alias("avg_duration_ms"),
            F.sum("total_tokens").alias("total_tokens"),
        )
        job_by_merchant.show(truncate=False)

        # ============================================================
        # STEP TIMING (Updated for Phase 1)
        # ============================================================
        logger.info("\n" + "=" * 60)
        logger.info("STEP TIMING (Including Phase 1)")
        logger.info("=" * 60)

        # Step names for both Phase 1 and Phase 2
        step_names = [
            # Phase 1 - Pattern computation
            "PatternComputation",
            "LearnLineItemPatterns",
            "BuildMerchantPatterns",
            "ollama_pattern_discovery",
            # Phase 2 - Receipt evaluation (root)
            "ReceiptEvaluation",
            # Phase 2 - unified architecture child traces
            "load_patterns",
            "build_visual_lines",
            "setup_llm",
            "currency_evaluation",
            "metadata_evaluation",
            "geometric_evaluation",
            "apply_phase1_corrections",
            "phase2_financial_validation",
            "phase3_llm_review",
            "upload_results",
            # Phase 2 - additional child traces
            "ComputePatterns",
            "DiscoverPatterns",
        ]

        steps = parsed.filter(F.col("name").isin(step_names))

        step_timing = (
            steps.groupBy("name")
            .agg(
                F.avg("duration_ms").alias("avg_duration_ms"),
                F.expr("percentile_approx(duration_ms, 0.5)").alias(
                    "p50_duration_ms"
                ),
                F.expr("percentile_approx(duration_ms, 0.95)").alias(
                    "p95_duration_ms"
                ),
                F.min("duration_ms").alias("min_duration_ms"),
                F.max("duration_ms").alias("max_duration_ms"),
                F.count("*").alias("total_runs"),
            )
            .orderBy("avg_duration_ms", ascending=False)
        )

        logger.info("\nStep timing (all phases):")
        step_timing.show(30, truncate=False)

        # ============================================================
        # TRACE HIERARCHY VALIDATION
        # ============================================================
        logger.info("\n" + "=" * 60)
        logger.info("TRACE HIERARCHY VALIDATION")
        logger.info("=" * 60)

        # Check PatternComputation hierarchy
        pattern_traces = parsed.filter(F.col("name") == "PatternComputation")
        if pattern_traces.count() > 0:
            pattern_id = pattern_traces.first()["id"]
            logger.info(f"\nPatternComputation trace: {pattern_id[:8]}...")

            children = parsed.filter(F.col("parent_run_id") == pattern_id)
            logger.info(f"Direct children ({children.count()}):")
            children.select("name", "id", "duration_ms").show(truncate=False)

        # Check ReceiptEvaluation hierarchies
        receipt_traces = parsed.filter(F.col("name") == "ReceiptEvaluation")
        logger.info(f"\nReceiptEvaluation traces: {receipt_traces.count()}")
        for row in receipt_traces.collect():
            receipt_id = row["id"]
            children = parsed.filter(F.col("parent_run_id") == receipt_id)
            logger.info(
                f"  {receipt_id[:8]}... has {children.count()} children"
            )

        parsed.unpersist()

    except Exception:
        logger.exception("Test failed")
        return 1
    finally:
        spark.stop()

    logger.info("\n" + "=" * 60)
    logger.info("TEST COMPLETE")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
