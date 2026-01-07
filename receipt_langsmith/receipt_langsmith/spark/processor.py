"""PySpark processor for LangSmith analytics.

This module provides the main processor class for running analytics
on LangSmith trace exports using PySpark on EMR Serverless.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.window import Window

from receipt_langsmith.spark.schemas import LANGSMITH_PARQUET_SCHEMA

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Cost per token (approximate, adjust as needed)
COST_PER_1K_PROMPT_TOKENS = 0.0015  # GPT-4o-mini input
COST_PER_1K_COMPLETION_TOKENS = 0.006  # GPT-4o-mini output


class LangSmithSparkProcessor:
    """PySpark processor for large-scale LangSmith analytics.

    This processor reads Parquet exports and computes various analytics:
    - Receipt-level metrics (duration, tokens, cost)
    - Step timing analysis (P50/P95/P99 latencies)
    - LLM decision analysis (VALID/INVALID/NEEDS_REVIEW counts)
    - Token usage over time

    Args:
        spark: SparkSession instance.

    Example:
        ```python
        spark = SparkSession.builder.appName("LangSmithAnalytics").getOrCreate()
        processor = LangSmithSparkProcessor(spark)

        df = processor.read_parquet("s3://bucket/traces/")
        parsed = processor.parse_json_fields(df)

        # Run analytics
        receipt_analytics = processor.compute_receipt_analytics(parsed)
        step_timing = processor.compute_step_timing(parsed)
        decisions = processor.compute_decision_analysis(parsed)

        # Write results
        processor.write_analytics(receipt_analytics, "s3://bucket/analytics/receipts/")
        ```
    """

    def __init__(self, spark: SparkSession):
        self.spark = spark

    def read_parquet(self, path: str) -> DataFrame:
        """Read Parquet files from S3 or local path.

        Uses PyArrow to handle nanosecond timestamps that Spark doesn't support.

        Args:
            path: S3 URI or local path to Parquet files.

        Returns:
            DataFrame with raw trace data.
        """
        import pyarrow.parquet as pq
        import pyarrow.fs as pafs

        logger.info("Reading Parquet from: %s", path)

        # Columns we need for analytics
        needed_columns = [
            "id", "trace_id", "name", "run_type", "status",
            "start_time", "end_time", "extra", "outputs",
            "total_tokens", "prompt_tokens", "completion_tokens",
        ]

        # Parse S3 path
        if path.startswith("s3://"):
            bucket_and_key = path[5:]
            bucket = bucket_and_key.split("/")[0]
            key = "/".join(bucket_and_key.split("/")[1:])
            fs = pafs.S3FileSystem(region="us-east-1")
            table = pq.read_table(
                f"{bucket}/{key}",
                filesystem=fs,
                columns=needed_columns,
            )
        else:
            table = pq.read_table(path, columns=needed_columns)

        # Convert nanosecond timestamps to microseconds (Spark-compatible)
        import pyarrow as pa

        new_schema_fields = []
        for field in table.schema:
            if pa.types.is_timestamp(field.type):
                # Convert to microsecond precision
                new_schema_fields.append(
                    pa.field(field.name, pa.timestamp("us", tz=None))
                )
            else:
                new_schema_fields.append(field)

        new_schema = pa.schema(new_schema_fields)
        table = table.cast(new_schema)

        # Convert to Pandas then to Spark with explicit schema
        pdf = table.to_pandas()

        # Define Spark schema for known columns
        from pyspark.sql.types import (
            StructType, StructField, StringType, LongType, TimestampType
        )

        spark_schema = StructType([
            StructField("id", StringType(), True),
            StructField("trace_id", StringType(), True),
            StructField("name", StringType(), True),
            StructField("run_type", StringType(), True),
            StructField("status", StringType(), True),
            StructField("start_time", TimestampType(), True),
            StructField("end_time", TimestampType(), True),
            StructField("extra", StringType(), True),
            StructField("outputs", StringType(), True),
            StructField("total_tokens", LongType(), True),
            StructField("prompt_tokens", LongType(), True),
            StructField("completion_tokens", LongType(), True),
        ])

        return self.spark.createDataFrame(pdf, schema=spark_schema)

    def parse_json_fields(self, df: DataFrame) -> DataFrame:
        """Parse JSON string columns and extract metadata.

        Extracts commonly used fields from the `extra` JSON:
        - execution_id
        - merchant_name
        - image_id
        - receipt_id

        Args:
            df: DataFrame with raw trace data.

        Returns:
            DataFrame with extracted metadata columns.
        """
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
                    F.unix_timestamp("end_time") - F.unix_timestamp("start_time")
                )
                * 1000,
            )
        )

    def compute_receipt_analytics(self, df: DataFrame) -> DataFrame:
        """Compute per-receipt analytics.

        Aggregates metrics for each unique (image_id, receipt_id) combination:
        - Total duration
        - Token usage
        - Run counts

        Args:
            df: DataFrame with parsed metadata.

        Returns:
            DataFrame with receipt-level analytics.
        """
        # Filter to ReceiptEvaluation traces (root traces)
        receipts = df.filter(F.col("name") == "ReceiptEvaluation")

        # Get all runs in each trace for aggregation
        all_runs = df.alias("all")
        trace_stats = (
            all_runs.groupBy("trace_id")
            .agg(
                F.sum("duration_ms").alias("total_duration_ms"),
                F.sum("total_tokens").alias("total_tokens"),
                F.sum("prompt_tokens").alias("prompt_tokens"),
                F.sum("completion_tokens").alias("completion_tokens"),
                F.count("*").alias("run_count"),
                F.sum(F.when(F.col("run_type") == "llm", 1).otherwise(0)).alias(
                    "llm_run_count"
                ),
            )
        )

        # Join with receipt metadata
        result = (
            receipts.join(trace_stats, "trace_id", "left")
            .select(
                F.col("metadata_merchant_name").alias("merchant_name"),
                F.col("metadata_image_id").alias("image_id"),
                F.col("metadata_receipt_id").alias("receipt_id"),
                F.col("metadata_execution_id").alias("execution_id"),
                "total_duration_ms",
                "total_tokens",
                "prompt_tokens",
                "completion_tokens",
                "run_count",
                "llm_run_count",
                F.col("status"),
            )
            .dropDuplicates(["image_id", "receipt_id", "execution_id"])
        )

        logger.info("Computed receipt analytics: %d rows", result.count())
        return result

    def compute_step_timing(self, df: DataFrame) -> DataFrame:
        """Compute timing breakdown by step name.

        Calculates percentile latencies (P50, P95, P99) for each step type.

        Args:
            df: DataFrame with parsed metadata.

        Returns:
            DataFrame with step timing statistics.
        """
        # Step names we care about
        step_names = [
            "ReceiptEvaluation",
            "EvaluateLabels",
            "EvaluateCurrencyLabels",
            "EvaluateMetadataLabels",
            "ValidateFinancialMath",
            "LLMReview",
        ]

        steps = df.filter(F.col("name").isin(step_names))

        result = steps.groupBy("name").agg(
            F.avg("duration_ms").alias("avg_duration_ms"),
            F.expr("percentile_approx(duration_ms, 0.5)").alias("p50_duration_ms"),
            F.expr("percentile_approx(duration_ms, 0.95)").alias("p95_duration_ms"),
            F.expr("percentile_approx(duration_ms, 0.99)").alias("p99_duration_ms"),
            F.min("duration_ms").alias("min_duration_ms"),
            F.max("duration_ms").alias("max_duration_ms"),
            F.count("*").alias("total_runs"),
            F.sum("total_tokens").alias("total_tokens"),
        )

        result = result.withColumnRenamed("name", "step_name")

        logger.info("Computed step timing: %d steps", result.count())
        return result

    def compute_decision_analysis(self, df: DataFrame) -> DataFrame:
        """Analyze LLM decisions (VALID/INVALID/NEEDS_REVIEW).

        Parses the outputs JSON to extract decision counts by:
        - Merchant
        - Label type (currency, metadata, financial)
        - Decision (VALID, INVALID, NEEDS_REVIEW)

        Args:
            df: DataFrame with parsed metadata.

        Returns:
            DataFrame with decision analysis.
        """
        # Filter to evaluator steps that have decisions
        evaluators = df.filter(
            F.col("name").isin(
                [
                    "EvaluateCurrencyLabels",
                    "EvaluateMetadataLabels",
                    "ValidateFinancialMath",
                ]
            )
        )

        # Extract decisions from outputs JSON
        # The structure is: outputs.all_decisions[].llm_review.decision
        with_decisions = evaluators.withColumn(
            "decisions_json",
            F.get_json_object(F.col("outputs"), "$.all_decisions"),
        ).withColumn(
            "decisions_count",
            F.get_json_object(F.col("outputs"), "$.decisions"),
        )

        # Extract decision counts directly from the decisions object
        result = (
            with_decisions.withColumn(
                "valid_count",
                F.coalesce(
                    F.get_json_object(F.col("decisions_count"), "$.VALID").cast(
                        "int"
                    ),
                    F.lit(0),
                ),
            )
            .withColumn(
                "invalid_count",
                F.coalesce(
                    F.get_json_object(F.col("decisions_count"), "$.INVALID").cast(
                        "int"
                    ),
                    F.lit(0),
                ),
            )
            .withColumn(
                "needs_review_count",
                F.coalesce(
                    F.get_json_object(
                        F.col("decisions_count"), "$.NEEDS_REVIEW"
                    ).cast("int"),
                    F.lit(0),
                ),
            )
        )

        # Map step name to label type
        result = result.withColumn(
            "label_type",
            F.when(F.col("name") == "EvaluateCurrencyLabels", "currency")
            .when(F.col("name") == "EvaluateMetadataLabels", "metadata")
            .when(F.col("name") == "ValidateFinancialMath", "financial")
            .otherwise("unknown"),
        )

        # Aggregate by merchant and label type
        aggregated = result.groupBy("metadata_merchant_name", "label_type").agg(
            F.sum("valid_count").alias("valid_total"),
            F.sum("invalid_count").alias("invalid_total"),
            F.sum("needs_review_count").alias("needs_review_total"),
            F.count("*").alias("evaluation_count"),
        )

        # Unpivot to get one row per decision type
        final = (
            aggregated.select(
                F.col("metadata_merchant_name").alias("merchant_name"),
                "label_type",
                F.explode(
                    F.array(
                        F.struct(
                            F.lit("VALID").alias("decision"),
                            F.col("valid_total").alias("count"),
                        ),
                        F.struct(
                            F.lit("INVALID").alias("decision"),
                            F.col("invalid_total").alias("count"),
                        ),
                        F.struct(
                            F.lit("NEEDS_REVIEW").alias("decision"),
                            F.col("needs_review_total").alias("count"),
                        ),
                    )
                ).alias("decision_struct"),
            )
            .select(
                "merchant_name",
                "label_type",
                F.col("decision_struct.decision").alias("decision"),
                F.col("decision_struct.count").alias("count"),
            )
            .filter(F.col("count") > 0)
        )

        logger.info("Computed decision analysis: %d rows", final.count())
        return final

    def compute_token_usage(self, df: DataFrame) -> DataFrame:
        """Compute token usage aggregates by date and merchant.

        Args:
            df: DataFrame with parsed metadata.

        Returns:
            DataFrame with daily token usage.
        """
        result = (
            df.withColumn("date", F.to_date("start_time"))
            .groupBy("date", "metadata_merchant_name")
            .agg(
                F.sum("total_tokens").alias("total_tokens"),
                F.sum("prompt_tokens").alias("prompt_tokens"),
                F.sum("completion_tokens").alias("completion_tokens"),
                F.count("*").alias("trace_count"),
            )
            .withColumn(
                "estimated_cost_usd",
                (
                    F.col("prompt_tokens") / 1000 * COST_PER_1K_PROMPT_TOKENS
                    + F.col("completion_tokens")
                    / 1000
                    * COST_PER_1K_COMPLETION_TOKENS
                ),
            )
            .withColumnRenamed("metadata_merchant_name", "merchant_name")
        )

        logger.info("Computed token usage: %d rows", result.count())
        return result

    def write_analytics(
        self,
        df: DataFrame,
        output_path: str,
        partition_by: Optional[list[str]] = None,
        mode: str = "overwrite",
    ) -> None:
        """Write analytics results to S3 or local path.

        Args:
            df: DataFrame to write.
            output_path: S3 URI or local path.
            partition_by: Columns to partition by.
            mode: Write mode ('overwrite', 'append').
        """
        logger.info("Writing analytics to: %s", output_path)

        writer = df.write.mode(mode)

        if partition_by:
            writer = writer.partitionBy(*partition_by)

        writer.parquet(output_path)

        logger.info("Analytics written successfully")
