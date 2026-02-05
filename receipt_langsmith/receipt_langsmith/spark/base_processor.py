"""Base Spark processor with shared IO utilities."""

from __future__ import annotations

import logging
from typing import Optional

from pyspark.sql import DataFrame, SparkSession

from receipt_langsmith.spark.trace_df import (
    TraceReadOptions,
    add_duration_ms,
    add_metadata_fields,
    read_parquet_df,
)
from receipt_langsmith.spark.utils import to_s3a

logger = logging.getLogger(__name__)


class BaseSparkProcessor:
    """Shared Spark processor behavior for LangSmith exports."""

    include_inputs: bool = False
    include_outputs: bool = True
    include_extra: bool = True
    include_tokens: bool = True

    def __init__(self, spark: SparkSession):
        self.spark = spark

    def read_parquet(self, path: str) -> DataFrame:
        """Read and normalize parquet trace exports."""
        options = TraceReadOptions(
            include_inputs=self.include_inputs,
            include_outputs=self.include_outputs,
            include_extra=self.include_extra,
            include_tokens=self.include_tokens,
        )
        return read_parquet_df(self.spark, path, options=options)

    def parse_json_fields(self, df: DataFrame) -> DataFrame:
        """Parse JSON fields common to all trace exports."""
        parsed = add_metadata_fields(df)
        parsed = add_duration_ms(parsed)
        return self._augment_parsed_fields(parsed)

    def _augment_parsed_fields(self, df: DataFrame) -> DataFrame:
        """Hook for subclasses to add project-specific fields."""
        return df

    def write_analytics(
        self,
        df: DataFrame,
        output_path: str,
        partition_by: Optional[list[str]] = None,
        mode: str = "overwrite",
    ) -> None:
        """Write analytics results to S3 or local path."""
        logger.info("Writing analytics to: %s", output_path)
        spark_path = to_s3a(output_path)

        writer = df.write.mode(mode)
        if partition_by:
            writer = writer.partitionBy(*partition_by)

        writer.parquet(spark_path)
        logger.info("Analytics written successfully")
