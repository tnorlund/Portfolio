"""Shared Spark DataFrame helpers for LangSmith trace data."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import LongType

from receipt_langsmith.spark.utils import TRACE_BASE_COLUMNS, to_s3a

logger = logging.getLogger(__name__)

TRACE_TOKEN_COLUMNS: tuple[str, ...] = (
    "total_tokens",
    "prompt_tokens",
    "completion_tokens",
)
TRACE_TIME_COLUMNS: tuple[str, ...] = ("start_time", "end_time")


@dataclass(frozen=True)
class TraceReadOptions:
    """Options for reading and normalizing trace data."""

    include_inputs: bool = False
    include_outputs: bool = True
    include_extra: bool = True
    include_tokens: bool = True


def trace_columns(options: TraceReadOptions) -> list[str]:
    """Return the ordered trace columns for selection."""
    columns: list[str] = [*TRACE_BASE_COLUMNS, *TRACE_TIME_COLUMNS]
    if options.include_extra:
        columns.append("extra")
    if options.include_inputs:
        columns.append("inputs")
    if options.include_outputs:
        columns.append("outputs")
    if options.include_tokens:
        columns.extend(TRACE_TOKEN_COLUMNS)
    return columns


def normalize_trace_df(
    df: DataFrame,
    options: TraceReadOptions,
) -> DataFrame:
    """Normalize trace DataFrame schema and timestamps."""
    available_columns = set(df.columns)

    if "start_time" in available_columns and isinstance(
        df.schema["start_time"].dataType, LongType
    ):
        df = df.withColumn(
            "start_time",
            (F.col("start_time") / 1_000_000_000).cast("timestamp"),
        ).withColumn(
            "end_time",
            (F.col("end_time") / 1_000_000_000).cast("timestamp"),
        )

    if "trace_id" not in available_columns:
        df = df.withColumn("trace_id", F.col("id"))

    if "parent_run_id" not in available_columns:
        df = df.withColumn("parent_run_id", F.lit(None).cast("string"))

    if "status" not in available_columns:
        df = df.withColumn("status", F.lit(None).cast("string"))

    if options.include_inputs and "inputs" not in available_columns:
        df = df.withColumn("inputs", F.lit(None).cast("string"))

    if options.include_outputs and "outputs" not in available_columns:
        df = df.withColumn("outputs", F.lit(None).cast("string"))

    if options.include_extra and "extra" not in available_columns:
        df = df.withColumn("extra", F.lit(None).cast("string"))

    if options.include_tokens:
        for column in TRACE_TOKEN_COLUMNS:
            if column not in available_columns:
                df = df.withColumn(column, F.lit(None).cast("long"))

    return df


def read_parquet_df(
    spark: SparkSession,
    path: str,
    *,
    options: TraceReadOptions | None = None,
) -> DataFrame:
    """Read and normalize LangSmith trace parquet exports."""
    if options is None:
        options = TraceReadOptions()
    logger.info("Reading Parquet from: %s", path)
    spark_path = to_s3a(path)
    df = spark.read.option("recursiveFileLookup", "true").parquet(spark_path)
    logger.info(
        "Available columns in parquet: %s", sorted(df.columns)
    )

    df = normalize_trace_df(df, options)
    df = df.select(*trace_columns(options))

    logger.info("Read Parquet with %d partitions", df.rdd.getNumPartitions())
    return df


def add_metadata_fields(df: DataFrame) -> DataFrame:
    """Extract common metadata fields from extra JSON."""
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
            F.get_json_object(
                F.col("extra"), "$.metadata.receipt_id"
            ).cast("int"),
        )
    )


def add_duration_ms(
    df: DataFrame,
    *,
    start_col: str = "start_time",
    end_col: str = "end_time",
) -> DataFrame:
    """Add duration_ms computed from start/end timestamps."""
    return df.withColumn(
        "duration_ms",
        (
            F.col(end_col).cast("double")
            - F.col(start_col).cast("double")
        )
        * 1000,
    )
