"""Shared CLI helpers for Spark jobs."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Callable
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from py4j.protocol import Py4JJavaError
from pyspark.sql import SparkSession
from pyspark.sql.utils import AnalysisException

SPARK_JOB_EXCEPTIONS = (
    AnalysisException,
    Py4JJavaError,
    ClientError,
    BotoCoreError,
    OSError,
    ValueError,
    TypeError,
    KeyError,
)


def configure_logging() -> None:
    """Configure default logging for Spark jobs."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def add_cache_bucket_arg(
    parser: argparse.ArgumentParser,
    *,
    help_text: str = "S3 bucket to write visualization cache",
) -> None:
    """Add the shared cache-bucket argument."""
    parser.add_argument(
        "--cache-bucket",
        required=True,
        help=help_text,
    )


def add_receipts_json_arg(
    parser: argparse.ArgumentParser,
    *,
    help_text: str = "S3 path to receipts-lookup.json (CDN keys from DynamoDB)",
) -> None:
    """Add the shared receipts-json argument."""
    parser.add_argument(
        "--receipts-json",
        required=True,
        help=help_text,
    )


def run_spark_job(
    spark: SparkSession,
    job: Callable[[], Any],
    *,
    logger: logging.Logger,
    error_message: str,
) -> int:
    """Run a Spark job with consistent exception handling."""
    try:
        result = job()
        if isinstance(result, int):
            return result
        return 0
    except SPARK_JOB_EXCEPTIONS:
        logger.exception(error_message)
        return 1
    finally:
        spark.stop()
