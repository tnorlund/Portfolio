#!/usr/bin/env python3
"""EMR Serverless job for QA Agent visualization cache generation.

Builds per-question cache JSONs from LangSmith trace data and
question results NDJSON. Output is consumed by the React QAAgentFlow
component via the /qa/visualization API.

Inputs:
    --parquet-input     S3 path to LangSmith Parquet exports
    --cache-bucket      S3 bucket for viz cache output
    --results-ndjson    S3 path to question-results.ndjson from Step Function
    --receipts-json     S3 path to receipts-lookup.json

Output:
    questions/question-{index}.json  (one per question)
    metadata.json                     (aggregate stats)
    latest.json                       (version pointer)

Usage:
    spark-submit \
        --conf spark.executor.memory=4g \
        qa_viz_cache_job.py \
        --parquet-input s3://bucket/traces/ \
        --cache-bucket viz-cache-bucket \
        --results-ndjson s3://bucket/qa-runs/abc/question-results.ndjson \
        --receipts-json s3://bucket/qa-runs/abc/receipts-lookup.json
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Any, Optional

import boto3
from pyspark.sql import SparkSession

from receipt_langsmith.spark.cli import (
    add_cache_bucket_arg,
    add_receipts_json_arg,
    configure_logging,
    run_spark_job,
)
from receipt_langsmith.spark.qa_viz_cache_helpers import (
    build_qa_baseline_profile,
    QACacheJobConfig,
    QACacheWriteContext,
    build_cache_files_from_parquet,
    load_question_results,
    load_receipts_lookup,
    qa_cache_config_from_args,
    write_cache_files,
    write_cache_from_ndjson,
)

configure_logging()
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="QA Agent visualization cache generator for EMR Serverless"
    )
    parser.add_argument(
        "--parquet-input",
        required=True,
        help="S3 path to LangSmith Parquet exports",
    )
    add_cache_bucket_arg(parser)
    parser.add_argument(
        "--results-ndjson",
        required=True,
        help="S3 path to question-results.ndjson from Step Function",
    )
    add_receipts_json_arg(parser)
    parser.add_argument(
        "--execution-id",
        default="",
        help="Execution ID for this run",
    )
    parser.add_argument(
        "--max-questions",
        type=int,
        default=50,
        help="Maximum number of questions to process (default: 50)",
    )
    return parser.parse_args()


def run_qa_cache_job(
    spark: SparkSession,
    *legacy_args: Any,
    config: Optional[QACacheJobConfig] = None,
    **legacy_kwargs: Any,
) -> None:
    """Main entry point for QA viz cache generation.

    Args:
        spark: Spark session.
        config: QACacheJobConfig for this run.
        legacy_args: Legacy positional args for backward compatibility.
        legacy_kwargs: Legacy keyword args for backward compatibility.
    """
    if config is None:
        config = QACacheJobConfig.from_legacy(
            *legacy_args,
            **legacy_kwargs,
        )
    elif legacy_args or legacy_kwargs:
        raise TypeError(
            "run_qa_cache_job received both config and legacy arguments"
        )

    s3_client = boto3.client("s3")
    write_ctx = QACacheWriteContext(
        s3_client=s3_client,
        cache_bucket=config.cache_bucket,
        execution_id=config.execution_id,
        langchain_project=config.langchain_project,
    )

    question_results = load_question_results(
        s3_client,
        config.results_ndjson,
    )
    if not question_results:
        logger.error(
            "No question results found in %s",
            config.results_ndjson,
        )
        return

    receipts_lookup = load_receipts_lookup(
        s3_client,
        config.receipts_json,
    )

    build_stats: dict[str, Any] = {}
    cache_files = build_cache_files_from_parquet(
        spark,
        config.parquet_input,
        question_results,
        receipts_lookup,
        config.max_questions,
        stats_out=build_stats,
    )
    source_mode = build_stats.get("source_mode", "parquet")
    if cache_files is None:
        run_profile = build_qa_baseline_profile(
            spark,
            execution_id=config.execution_id,
            source_mode=source_mode,
            question_results_count=len(question_results),
            receipts_lookup_count=len(receipts_lookup),
            cache_files_count=min(len(question_results), config.max_questions),
            max_questions=config.max_questions,
            build_stats=build_stats,
        )
        write_cache_from_ndjson(
            write_ctx,
            question_results,
            receipts_lookup,
            config.max_questions,
            run_profile=run_profile,
        )
        return

    run_profile = build_qa_baseline_profile(
        spark,
        execution_id=config.execution_id,
        source_mode=source_mode,
        question_results_count=len(question_results),
        receipts_lookup_count=len(receipts_lookup),
        cache_files_count=len(cache_files),
        max_questions=config.max_questions,
        build_stats=build_stats,
    )
    write_cache_files(
        write_ctx,
        cache_files,
        question_results,
        run_profile=run_profile,
    )


def main() -> int:
    """Entry point for standalone execution."""
    args = parse_args()

    spark = (
        SparkSession.builder.appName("qa-viz-cache-generator")
        .config("spark.sql.legacy.parquet.nanosAsLong", "true")
        .getOrCreate()
    )

    def job() -> None:
        config = qa_cache_config_from_args(args)
        run_qa_cache_job(spark, config=config)

    return run_spark_job(
        spark,
        job,
        logger=logger,
        error_message="Job failed",
    )


if __name__ == "__main__":
    sys.exit(main())
