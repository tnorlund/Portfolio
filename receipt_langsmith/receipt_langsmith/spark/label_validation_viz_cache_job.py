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
    spark-submit \
        --conf spark.executor.memory=4g \
        --conf spark.executor.cores=2 \
        --conf spark.sql.legacy.parquet.nanosAsLong=true \
        label_validation_viz_cache_job.py \
        --parquet-bucket langsmith-export-bucket \
        --parquet-prefix traces/export_id=<export-id>/ \
        --cache-bucket viz-cache-bucket \
        --receipts-json s3://cache-bucket/receipts-lookup.json
"""

from __future__ import annotations

import argparse
import sys

from receipt_langsmith.spark.cli import (
    add_cache_bucket_arg,
    add_receipts_json_arg,
    configure_logging,
)
from receipt_langsmith.spark.label_validation_viz_cache_helpers import (
    run_label_validation_cache,
)

configure_logging()


# --- Argument Parsing ---


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Label Validation visualization cache generator"
    )
    parser.add_argument(
        "--parquet-bucket",
        required=True,
        help="S3 bucket containing LangSmith Parquet exports",
    )
    parser.add_argument(
        "--parquet-prefix",
        required=True,
        help=(
            "Parquet prefix for a specific export, e.g. "
            "'traces/export_id=<export-id>/'"
        ),
    )
    add_cache_bucket_arg(parser)
    add_receipts_json_arg(parser)
    parser.add_argument(
        "--max-receipts",
        type=int,
        default=50,
        help="Maximum receipts to include in cache (default: 50)",
    )

    return parser.parse_args()


# --- Main ---


def main() -> int:
    """Main entry point."""
    args = parse_args()
    return run_label_validation_cache(args)


if __name__ == "__main__":
    sys.exit(main())
