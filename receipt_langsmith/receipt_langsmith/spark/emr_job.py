#!/usr/bin/env python3
"""EMR Serverless job entry point for LangSmith analytics.

This script is the entry point for running LangSmith analytics on
EMR Serverless. It supports different job types for computing
various analytics.

Usage:
    spark-submit \\
        --conf spark.executor.memory=4g \\
        --conf spark.executor.cores=2 \\
        emr_job.py \\
        --input s3://bucket/traces/ \\
        --output s3://bucket/analytics/ \\
        --job-type all

Job Types:
    receipt-analytics: Per-receipt metrics (duration, tokens, cost)
    step-timing: Step latency percentiles (P50/P95/P99)
    decision-analysis: LLM decision breakdown (VALID/INVALID/NEEDS_REVIEW)
    token-usage: Daily token usage by merchant
    all: Run all analytics
"""

from __future__ import annotations

import argparse
import logging
import sys

from pyspark.sql import SparkSession

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="LangSmith analytics job for EMR Serverless"
    )

    parser.add_argument(
        "--input",
        required=True,
        help="S3 input path for Parquet traces",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="S3 output path for analytics results",
    )
    parser.add_argument(
        "--job-type",
        choices=[
            "receipt-analytics",
            "step-timing",
            "decision-analysis",
            "token-usage",
            "all",
        ],
        default="all",
        help="Type of analytics to run",
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

    return parser.parse_args()


def main() -> int:
    """Main entry point."""
    args = parse_args()

    logger.info("Starting LangSmith analytics job")
    logger.info("Input: %s", args.input)
    logger.info("Output: %s", args.output)
    logger.info("Job type: %s", args.job_type)

    # Initialize Spark
    spark = (
        SparkSession.builder.appName(f"LangSmithAnalytics-{args.job_type}")
        .getOrCreate()
    )

    try:
        # Import processor (requires pyspark)
        from receipt_langsmith.spark.processor import LangSmithSparkProcessor

        processor = LangSmithSparkProcessor(spark)

        # Read and parse data
        logger.info("Reading Parquet data...")
        df = processor.read_parquet(args.input)

        logger.info("Parsing JSON fields...")
        parsed = processor.parse_json_fields(df)

        # Cache for reuse if running multiple analytics
        cached = args.job_type == "all"
        if cached:
            parsed.cache()

        try:
            # Run requested analytics
            if args.job_type in ("receipt-analytics", "all"):
                logger.info("Computing receipt analytics...")
                analytics = processor.compute_receipt_analytics(parsed)

                partition_by = ["merchant_name"] if args.partition_by_merchant else None
                processor.write_analytics(
                    analytics,
                    f"{args.output}/receipt_analytics/",
                    partition_by=partition_by,
                )
                logger.info("Receipt analytics complete")

            if args.job_type in ("step-timing", "all"):
                logger.info("Computing step timing...")
                timing = processor.compute_step_timing(parsed)

                processor.write_analytics(
                    timing,
                    f"{args.output}/step_timing/",
                )
                logger.info("Step timing complete")

            if args.job_type in ("decision-analysis", "all"):
                logger.info("Computing decision analysis...")
                decisions = processor.compute_decision_analysis(parsed)

                partition_by = ["merchant_name"] if args.partition_by_merchant else None
                processor.write_analytics(
                    decisions,
                    f"{args.output}/decision_analysis/",
                    partition_by=partition_by,
                )
                logger.info("Decision analysis complete")

            if args.job_type in ("token-usage", "all"):
                logger.info("Computing token usage...")
                tokens = processor.compute_token_usage(parsed)

                partition_by = ["date"] if args.partition_by_date else None
                processor.write_analytics(
                    tokens,
                    f"{args.output}/token_usage/",
                    partition_by=partition_by,
                )
                logger.info("Token usage complete")
        finally:
            # Release cached data to free memory
            if cached:
                parsed.unpersist()

    except Exception:
        logger.exception("Analytics job failed")
        return 1

    else:
        logger.info("All analytics complete!")
        return 0

    finally:
        spark.stop()


if __name__ == "__main__":
    sys.exit(main())
