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
from botocore.exceptions import BotoCoreError, ClientError
from py4j.protocol import Py4JJavaError
from pyspark.sql import SparkSession
from pyspark.sql.utils import AnalysisException

from receipt_langsmith.spark.qa_viz_cache_helpers import (
    QACacheJobConfig,
    QACacheWriteContext,
    build_cache_files_from_parquet,
    load_question_results,
    load_receipts_lookup,
    write_cache_files,
    write_cache_from_ndjson,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
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
    parser.add_argument(
        "--cache-bucket",
        required=True,
        help="S3 bucket to write visualization cache",
    )
    parser.add_argument(
        "--results-ndjson",
        required=True,
        help="S3 path to question-results.ndjson from Step Function",
    )
    parser.add_argument(
        "--receipts-json",
        required=True,
        help="S3 path to receipts-lookup.json (CDN keys from DynamoDB)",
    )
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

    cache_files = build_cache_files_from_parquet(
        spark,
        config.parquet_input,
        question_results,
        receipts_lookup,
        config.max_questions,
    )
    if cache_files is None:
        write_cache_from_ndjson(
            write_ctx,
            question_results,
            receipts_lookup,
            config.max_questions,
        )
        return

    write_cache_files(write_ctx, cache_files, question_results)


def main() -> int:
    """Entry point for standalone execution."""
    args = parse_args()

    spark = (
        SparkSession.builder.appName("qa-viz-cache-generator")
        .config("spark.sql.legacy.parquet.nanosAsLong", "true")
        .getOrCreate()
    )

    try:
        config = QACacheJobConfig(
            parquet_input=args.parquet_input,
            cache_bucket=args.cache_bucket,
            results_ndjson=args.results_ndjson,
            receipts_json=args.receipts_json,
            execution_id=args.execution_id,
            max_questions=args.max_questions,
        )
        run_qa_cache_job(spark, config=config)
        return 0
    except (
        AnalysisException,
        Py4JJavaError,
        ClientError,
        BotoCoreError,
        OSError,
        ValueError,
        TypeError,
        KeyError,
    ):
        logger.exception("Job failed")
        return 1
    finally:
        spark.stop()


if __name__ == "__main__":
    sys.exit(main())
