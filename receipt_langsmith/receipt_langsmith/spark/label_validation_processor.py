"""PySpark processor for receipt-label-validation analytics.

This module provides analytics for the receipt-label-validation LangSmith
project, including receipt-level metrics, step timing, validation decisions,
and merchant resolution success rates.
"""

from __future__ import annotations

import logging

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from receipt_langsmith.spark.analytics_helpers import (
    build_trace_stats,
    duration_stats,
    step_timing_stats,
)
from receipt_langsmith.spark.base_processor import BaseSparkProcessor

logger = logging.getLogger(__name__)

# Step name patterns for the receipt-label-validation project
STEP_PATTERNS = {
    "s3": ["s3_download_lines_snapshot", "s3_download_words_snapshot"],
    "embedding": ["openai_embed_lines", "openai_embed_words"],
    "chroma": ["label_validation_chroma", "chromadb_upsert"],
    "llm": ["llm_batch_validation", "label_validation_llm", "ChatOllama"],
    "merchant": [
        "merchant_resolution_chroma_phone",
        "merchant_resolution_chroma_address",
        "merchant_resolution_chroma_text",
    ],
}

# All step names flattened
ALL_STEP_NAMES = [name for names in STEP_PATTERNS.values() for name in names]

# Cost per token (text-embedding-3-small)
COST_PER_1K_EMBEDDING_TOKENS = 0.00002


class LabelValidationSparkProcessor(BaseSparkProcessor):
    """PySpark processor for receipt-label-validation project analytics.

    This processor reads Parquet exports from the receipt-label-validation
    LangSmith project and computes:
    - Receipt-level metrics (duration, tokens, costs, validation counts)
    - Step timing analysis (S3, embedding, Chroma, LLM, merchant resolution)
    - Label validation decision analysis by source and decision type
    - Merchant resolution success rates by tier

    Args:
        spark: SparkSession instance.

    Example:
        ```python
        spark = SparkSession.builder.appName(
            "LabelValidationAnalytics"
        ).getOrCreate()
        processor = LabelValidationSparkProcessor(spark)

        df = processor.read_parquet("s3://bucket/traces/")
        parsed = processor.parse_json_fields(df)

        # Run analytics
        receipt_metrics = processor.compute_receipt_metrics(parsed)
        step_timing = processor.compute_step_timing(parsed)
        decisions = processor.compute_validation_decisions(parsed)
        merchant = processor.compute_merchant_resolution_rates(parsed)
        ```
    """

    include_inputs = True

    def _augment_parsed_fields(self, df: DataFrame) -> DataFrame:
        """Add receipt-label-validation specific parsed fields."""
        return (
            df.withColumn(
                "validation_source",
                F.get_json_object(F.col("outputs"), "$.validation_source"),
            )
            .withColumn(
                "decision",
                F.get_json_object(F.col("outputs"), "$.decision"),
            )
            .withColumn(
                "confidence",
                F.get_json_object(F.col("outputs"), "$.confidence").cast(
                    "double"
                ),
            )
            .withColumn(
                "predicted_label",
                F.get_json_object(F.col("outputs"), "$.predicted_label"),
            )
            .withColumn(
                "resolution_tier",
                F.get_json_object(F.col("outputs"), "$.resolution_tier"),
            )
            .withColumn(
                "merchant_found",
                F.get_json_object(F.col("outputs"), "$.found").cast("boolean"),
            )
        )

    def compute_receipt_metrics(self, df: DataFrame) -> DataFrame:
        """Compute per-receipt analytics for receipt_processing traces.

        Aggregates metrics for each unique (image_id, receipt_id) combination:
        - Total duration and breakdown by step type
        - Token usage
        - Validation counts
        - Merchant resolution results

        Args:
            df: DataFrame with parsed metadata.

        Returns:
            DataFrame with receipt-level analytics.
        """
        # Filter to receipt_processing root traces
        receipts = df.filter(F.col("name") == "receipt_processing")

        # Get all runs in each trace for aggregation
        extra_aggs = [
            F.sum(F.when(F.col("name").like("s3_%"), 1).otherwise(0)).alias(
                "s3_runs"
            ),
            F.sum(
                F.when(F.col("name").like("openai_embed%"), 1).otherwise(0)
            ).alias("embed_runs"),
            F.sum(
                F.when(F.col("name").like("label_validation%"), 1).otherwise(0)
            ).alias("validation_runs"),
            F.sum(
                F.when(
                    F.col("name").like("merchant_resolution%"), 1
                ).otherwise(0)
            ).alias("merchant_runs"),
            F.sum(
                F.when(
                    F.col("name").like("s3_%"), F.col("duration_ms")
                ).otherwise(0)
            ).alias("s3_duration_ms"),
            F.sum(
                F.when(
                    F.col("name").like("openai_embed%"), F.col("duration_ms")
                ).otherwise(0)
            ).alias("embed_duration_ms"),
            F.sum(
                F.when(
                    F.col("name") == "label_validation_chroma",
                    F.col("duration_ms"),
                ).otherwise(0)
            ).alias("chroma_duration_ms"),
            F.sum(
                F.when(
                    F.col("name").isin(
                        ["llm_batch_validation", "label_validation_llm"]
                    ),
                    F.col("duration_ms"),
                ).otherwise(0)
            ).alias("llm_duration_ms"),
            F.sum(
                F.when(
                    F.col("name").like("merchant_resolution%"),
                    F.col("duration_ms"),
                ).otherwise(0)
            ).alias("merchant_duration_ms"),
        ]
        trace_stats = build_trace_stats(df, extra_aggs=extra_aggs)

        # Extract root trace output fields
        receipts_with_outputs = (
            receipts.withColumn(
                "success",
                F.get_json_object(F.col("outputs"), "$.success").cast(
                    "boolean"
                ),
            )
            .withColumn(
                "words_count",
                F.get_json_object(F.col("outputs"), "$.words_count").cast(
                    "int"
                ),
            )
            .withColumn(
                "lines_count",
                F.get_json_object(F.col("outputs"), "$.lines_count").cast(
                    "int"
                ),
            )
            .withColumn(
                "labels_validated",
                F.get_json_object(F.col("outputs"), "$.labels_validated").cast(
                    "int"
                ),
            )
            .withColumn(
                "labels_corrected",
                F.get_json_object(F.col("outputs"), "$.labels_corrected").cast(
                    "int"
                ),
            )
            .withColumn(
                "chroma_validated",
                F.get_json_object(F.col("outputs"), "$.chroma_validated").cast(
                    "int"
                ),
            )
            .withColumn(
                "llm_validated",
                F.get_json_object(F.col("outputs"), "$.llm_validated").cast(
                    "int"
                ),
            )
            .withColumn(
                "merchant_found_root",
                F.get_json_object(F.col("outputs"), "$.merchant_found").cast(
                    "boolean"
                ),
            )
            .withColumn(
                "merchant_name_root",
                F.get_json_object(F.col("outputs"), "$.merchant_name"),
            )
            .withColumn(
                "merchant_resolution_tier",
                F.get_json_object(
                    F.col("outputs"), "$.merchant_resolution_tier"
                ),
            )
            .withColumn(
                "merchant_confidence",
                F.get_json_object(
                    F.col("outputs"), "$.merchant_confidence"
                ).cast("double"),
            )
        )

        # Join with trace stats
        result = (
            receipts_with_outputs.join(trace_stats, "trace_id", "left")
            .select(
                F.col("metadata_image_id").alias("image_id"),
                F.col("metadata_receipt_id").alias("receipt_id"),
                F.coalesce(
                    F.col("merchant_name_root"),
                    F.col("metadata_merchant_name"),
                ).alias("merchant_name"),
                F.col("metadata_execution_id").alias("execution_id"),
                "total_duration_ms",
                F.col("s3_duration_ms").alias("s3_download_duration_ms"),
                F.col("embed_duration_ms").alias("embedding_duration_ms"),
                F.col("chroma_duration_ms").alias(
                    "chroma_validation_duration_ms"
                ),
                F.col("llm_duration_ms").alias("llm_validation_duration_ms"),
                F.col("merchant_duration_ms").alias(
                    "merchant_resolution_duration_ms"
                ),
                "words_count",
                "lines_count",
                "labels_validated",
                "labels_corrected",
                "chroma_validated",
                "llm_validated",
                F.col("merchant_found_root").alias("merchant_found"),
                "merchant_resolution_tier",
                "merchant_confidence",
                "total_tokens",
                "prompt_tokens",
                "completion_tokens",
                F.col("status"),
            )
            .dropDuplicates(["image_id", "receipt_id", "execution_id"])
        )

        logger.info("Computed receipt metrics")
        return result

    def compute_step_timing(self, df: DataFrame) -> DataFrame:
        """Compute timing breakdown by step name.

        Calculates percentile latencies (P50, P95, P99) for each step in the
        receipt-label-validation pipeline.

        Args:
            df: DataFrame with parsed metadata.

        Returns:
            DataFrame with step timing statistics.
        """
        # Build step type mapping
        step_type_mapping = {}
        for step_type, names in STEP_PATTERNS.items():
            for name in names:
                step_type_mapping[name] = step_type

        # Filter to relevant steps
        steps = df.filter(F.col("name").isin(ALL_STEP_NAMES))

        # Add step type column using when/otherwise chain
        step_type_expr = F.lit("unknown")
        for name, stype in step_type_mapping.items():
            step_type_expr = F.when(F.col("name") == name, stype).otherwise(
                step_type_expr
            )

        steps = steps.withColumn("step_type", step_type_expr)

        result = (
            step_timing_stats(steps, group_cols=["name", "step_type"])
            .withColumnRenamed("name", "step_name")
        )

        logger.info("Computed step timing")
        return result

    def compute_validation_decisions(self, df: DataFrame) -> DataFrame:
        """Analyze label validation decisions by source and type.

        Groups decisions by:
        - validation_source (chroma/llm)
        - decision (valid/invalid/needs_review)
        - predicted_label

        Args:
            df: DataFrame with parsed metadata.

        Returns:
            DataFrame with decision analysis.
        """
        # Filter to label_validation traces
        validations = df.filter(
            F.col("name").isin(
                ["label_validation_chroma", "label_validation_llm"]
            )
        )

        # Extract source from name if not in outputs
        validations = validations.withColumn(
            "source",
            F.coalesce(
                F.col("validation_source"),
                F.when(F.col("name") == "label_validation_chroma", "chroma")
                .when(F.col("name") == "label_validation_llm", "llm")
                .otherwise("unknown"),
            ),
        )

        result = (
            validations.groupBy("source", "decision", "predicted_label")
            .agg(
                F.count("*").alias("count"),
                F.avg("confidence").alias("avg_confidence"),
            )
            .withColumnRenamed("source", "validation_source")
            .withColumnRenamed("predicted_label", "label_type")
        )

        logger.info("Computed validation decisions")
        return result

    def compute_merchant_resolution_rates(self, df: DataFrame) -> DataFrame:
        """Compute merchant resolution success rates by tier.

        Analyzes phone, address, and text resolution attempts and
        calculates success rates and average confidence.

        Args:
            df: DataFrame with parsed metadata.

        Returns:
            DataFrame with merchant resolution statistics.
        """
        # Filter to merchant resolution traces
        merchant = df.filter(
            F.col("name").like("merchant_resolution_chroma_%")
        )

        # Extract tier from name if not in outputs
        merchant = merchant.withColumn(
            "tier",
            F.coalesce(
                F.col("resolution_tier"),
                F.regexp_extract(
                    F.col("name"), r"merchant_resolution_chroma_(\w+)", 1
                ),
            ),
        )

        result = (
            merchant.groupBy("tier")
            .agg(
                F.sum(F.when(F.col("merchant_found"), 1).otherwise(0)).alias(
                    "success_count"
                ),
                F.sum(
                    F.when(~F.col("merchant_found"), 1).otherwise(0)
                ).alias("failure_count"),
                F.avg(
                    F.when(F.col("merchant_found"), F.col("confidence"))
                ).alias("avg_confidence"),
                F.count("*").alias("total_attempts"),
            )
            .withColumn(
                "success_rate",
                F.col("success_count") / F.col("total_attempts"),
            )
            .withColumnRenamed("tier", "resolution_tier")
        )

        logger.info("Computed merchant resolution rates")
        return result

    def compute_s3_download_metrics(self, df: DataFrame) -> DataFrame:
        """Compute S3 download metrics for bandwidth analysis.

        Analyzes download times for lines and words snapshots to help
        identify performance bottlenecks.

        Args:
            df: DataFrame with parsed metadata.

        Returns:
            DataFrame with S3 download statistics.
        """
        s3_downloads = df.filter(F.col("name").like("s3_download_%_snapshot"))

        # Extract collection from name
        s3_downloads = s3_downloads.withColumn(
            "collection",
            F.when(F.col("name") == "s3_download_lines_snapshot", "lines")
            .when(F.col("name") == "s3_download_words_snapshot", "words")
            .otherwise("unknown"),
        )

        result = duration_stats(
            s3_downloads,
            group_cols=["collection"],
            percentiles=(0.5, 0.95),
            include_tokens=False,
            count_alias="total_downloads",
        )

        logger.info("Computed S3 download metrics")
        return result
