"""PySpark processor for LangSmith analytics.

This module provides the main processor class for running analytics
on LangSmith trace exports using PySpark on EMR Serverless.
"""

from __future__ import annotations

import logging

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from receipt_langsmith.spark.analytics_helpers import (
    build_trace_stats,
    step_timing_stats,
)
from receipt_langsmith.spark.base_processor import BaseSparkProcessor

logger = logging.getLogger(__name__)

# Cost per token (approximate, adjust as needed)
COST_PER_1K_PROMPT_TOKENS = 0.0015  # GPT-4o-mini input
COST_PER_1K_COMPLETION_TOKENS = 0.006  # GPT-4o-mini output


class LangSmithSparkProcessor(BaseSparkProcessor):
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
        spark = SparkSession.builder.appName(
            "LangSmithAnalytics"
        ).getOrCreate()
        processor = LangSmithSparkProcessor(spark)

        df = processor.read_parquet("s3://bucket/traces/")
        parsed = processor.parse_json_fields(df)

        # Run analytics
        receipt_analytics = processor.compute_receipt_analytics(parsed)
        step_timing = processor.compute_step_timing(parsed)
        decisions = processor.compute_decision_analysis(parsed)

        # Write results
        processor.write_analytics(
            receipt_analytics, "s3://bucket/analytics/receipts/"
        )
        ```
    """

    def compute_job_analytics(self, df: DataFrame) -> DataFrame:
        """Compute per-job analytics for both Phase 1 and Phase 2.

        Identifies two job types:
        - phase1_patterns: PatternComputation traces (per-merchant pattern
          learning)
        - phase2_evaluation: ReceiptEvaluation traces (per-receipt
          evaluation)

        Aggregates metrics for each job type:
        - Job count
        - Duration statistics (avg, min, max)
        - Token usage

        Args:
            df: DataFrame with parsed metadata.

        Returns:
            DataFrame with job-level analytics.
        """
        # Filter to root job traces
        jobs = df.filter(
            (F.col("name") == "PatternComputation")
            | (F.col("name") == "ReceiptEvaluation")
        )

        # Add job_type column
        jobs_with_type = jobs.withColumn(
            "job_type",
            F.when(
                F.col("name") == "PatternComputation", "phase1_patterns"
            ).when(F.col("name") == "ReceiptEvaluation", "phase2_evaluation"),
        )

        # Aggregate by job type
        result = jobs_with_type.groupBy("job_type").agg(
            F.count("*").alias("job_count"),
            F.avg("duration_ms").alias("avg_duration_ms"),
            F.min("duration_ms").alias("min_duration_ms"),
            F.max("duration_ms").alias("max_duration_ms"),
            F.expr("percentile_approx(duration_ms, 0.5)").alias(
                "p50_duration_ms"
            ),
            F.expr("percentile_approx(duration_ms, 0.95)").alias(
                "p95_duration_ms"
            ),
            F.sum("total_tokens").alias("total_tokens"),
        )

        logger.info("Computed job analytics")
        return result

    def compute_job_analytics_by_merchant(self, df: DataFrame) -> DataFrame:
        """Compute per-job analytics grouped by merchant.

        Provides merchant-level breakdown for both job types.

        Args:
            df: DataFrame with parsed metadata.

        Returns:
            DataFrame with job analytics by merchant.
        """
        # Filter to root job traces
        jobs = df.filter(
            (F.col("name") == "PatternComputation")
            | (F.col("name") == "ReceiptEvaluation")
        )

        # Add job_type column
        jobs_with_type = jobs.withColumn(
            "job_type",
            F.when(
                F.col("name") == "PatternComputation", "phase1_patterns"
            ).when(F.col("name") == "ReceiptEvaluation", "phase2_evaluation"),
        )

        # Aggregate by job type and merchant
        result = jobs_with_type.groupBy(
            "job_type", "metadata_merchant_name"
        ).agg(
            F.count("*").alias("job_count"),
            F.avg("duration_ms").alias("avg_duration_ms"),
            F.min("duration_ms").alias("min_duration_ms"),
            F.max("duration_ms").alias("max_duration_ms"),
            F.sum("total_tokens").alias("total_tokens"),
        )

        result = result.withColumnRenamed(
            "metadata_merchant_name", "merchant_name"
        )

        logger.info("Computed job analytics by merchant")
        return result

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
        trace_stats = build_trace_stats(df, include_llm_runs=True)

        # Join with receipt metadata
        # Select only needed columns from receipts to avoid duplicate columns
        # (receipts has total_tokens from original df, trace_stats has
        # aggregated total_tokens)
        receipts_subset = receipts.select(
            "trace_id",
            F.col("metadata_merchant_name").alias("merchant_name"),
            F.col("metadata_image_id").alias("image_id"),
            F.col("metadata_receipt_id").alias("receipt_id"),
            F.col("metadata_execution_id").alias("execution_id"),
            "status",
        )
        result = (
            receipts_subset.join(trace_stats, "trace_id", "left")
            .select(
                "merchant_name",
                "image_id",
                "receipt_id",
                "execution_id",
                "total_duration_ms",
                "total_tokens",
                "prompt_tokens",
                "completion_tokens",
                "run_count",
                "llm_run_count",
                "status",
            )
            .dropDuplicates(["image_id", "receipt_id", "execution_id"])
        )

        logger.info("Computed receipt analytics")
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
        # Includes Phase 1 (pattern computation), old multi-Lambda, and new
        # unified architecture
        step_names = [
            # Phase 1 - Pattern computation (per-merchant)
            "PatternComputation",
            "LearnLineItemPatterns",
            "BuildMerchantPatterns",
            "llm_pattern_discovery",  # Current trace name (provider-agnostic)
            "ollama_pattern_discovery",  # Legacy trace name (historical data)
            # Phase 2 - Root trace (both architectures)
            "ReceiptEvaluation",
            # Phase 2 - Old multi-Lambda architecture
            "EvaluateLabels",
            "EvaluateCurrencyLabels",
            "EvaluateMetadataLabels",
            "ValidateFinancialMath",
            "LLMReview",
            # Phase 2 - New unified architecture child traces
            "load_patterns",
            "build_visual_lines",
            "setup_llm",
            "currency_evaluation",
            "metadata_evaluation",
            "geometric_evaluation",
            "phase1_concurrent_evaluations",
            "apply_phase1_corrections",
            "phase2_financial_validation",
            "phase3_llm_review",
            "upload_results",
            # Phase 2 - Additional child traces (virtual spans from shared
            # computation)
            "ComputePatterns",
            "DiscoverPatterns",
        ]

        steps = df.filter(F.col("name").isin(step_names))

        result = step_timing_stats(steps, group_cols=["name"])

        result = result.withColumnRenamed("name", "step_name")

        logger.info("Computed step timing")
        return result

    def compute_decision_analysis(self, df: DataFrame) -> DataFrame:
        """Analyze LLM decisions (VALID/INVALID/NEEDS_REVIEW).

        Parses the outputs JSON to extract decision counts by:
        - Merchant
        - Label type (currency, metadata, financial)
        - Decision (VALID, INVALID, NEEDS_REVIEW)

        Supports two formats:
        1. Old multi-Lambda: Separate EvaluateCurrencyLabels,
           EvaluateMetadataLabels, ValidateFinancialMath traces with
           outputs.decisions = {VALID, INVALID, ...}
        2. New unified: ReceiptEvaluation trace with outputs.decisions = {
               currency: {VALID, ...}, metadata: {VALID, ...},
               financial: {VALID, ...}
           }

        Args:
            df: DataFrame with parsed metadata.

        Returns:
            DataFrame with decision analysis.
        """
        # Format 1: Old multi-Lambda architecture (separate traces per
        # evaluator)
        old_evaluators = df.filter(
            F.col("name").isin(
                [
                    "EvaluateCurrencyLabels",
                    "EvaluateMetadataLabels",
                    "ValidateFinancialMath",
                ]
            )
        )

        old_with_decisions = old_evaluators.withColumn(
            "decisions_count",
            F.get_json_object(F.col("outputs"), "$.decisions"),
        )

        old_result = (
            old_with_decisions.withColumn(
                "valid_count",
                F.coalesce(
                    F.get_json_object(
                        F.col("decisions_count"), "$.VALID"
                    ).cast("int"),
                    F.lit(0),
                ),
            )
            .withColumn(
                "invalid_count",
                F.coalesce(
                    F.get_json_object(
                        F.col("decisions_count"), "$.INVALID"
                    ).cast("int"),
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

        old_result = old_result.withColumn(
            "label_type",
            F.when(F.col("name") == "EvaluateCurrencyLabels", "currency")
            .when(F.col("name") == "EvaluateMetadataLabels", "metadata")
            .when(F.col("name") == "ValidateFinancialMath", "financial")
            .otherwise("unknown"),
        )

        # Format 2: New unified architecture (ReceiptEvaluation with nested
        # decisions)
        unified_receipts = df.filter(F.col("name") == "ReceiptEvaluation")

        # Extract nested decision counts for each label type
        # Structure: outputs.decisions.currency.VALID, etc.
        unified_currency = unified_receipts.select(
            F.col("metadata_merchant_name"),
            F.lit("currency").alias("label_type"),
            F.coalesce(
                F.get_json_object(
                    F.col("outputs"), "$.decisions.currency.VALID"
                ).cast("int"),
                F.lit(0),
            ).alias("valid_count"),
            F.coalesce(
                F.get_json_object(
                    F.col("outputs"), "$.decisions.currency.INVALID"
                ).cast("int"),
                F.lit(0),
            ).alias("invalid_count"),
            F.coalesce(
                F.get_json_object(
                    F.col("outputs"), "$.decisions.currency.NEEDS_REVIEW"
                ).cast("int"),
                F.lit(0),
            ).alias("needs_review_count"),
        )

        unified_metadata = unified_receipts.select(
            F.col("metadata_merchant_name"),
            F.lit("metadata").alias("label_type"),
            F.coalesce(
                F.get_json_object(
                    F.col("outputs"), "$.decisions.metadata.VALID"
                ).cast("int"),
                F.lit(0),
            ).alias("valid_count"),
            F.coalesce(
                F.get_json_object(
                    F.col("outputs"), "$.decisions.metadata.INVALID"
                ).cast("int"),
                F.lit(0),
            ).alias("invalid_count"),
            F.coalesce(
                F.get_json_object(
                    F.col("outputs"), "$.decisions.metadata.NEEDS_REVIEW"
                ).cast("int"),
                F.lit(0),
            ).alias("needs_review_count"),
        )

        unified_financial = unified_receipts.select(
            F.col("metadata_merchant_name"),
            F.lit("financial").alias("label_type"),
            F.coalesce(
                F.get_json_object(
                    F.col("outputs"), "$.decisions.financial.VALID"
                ).cast("int"),
                F.lit(0),
            ).alias("valid_count"),
            F.coalesce(
                F.get_json_object(
                    F.col("outputs"), "$.decisions.financial.INVALID"
                ).cast("int"),
                F.lit(0),
            ).alias("invalid_count"),
            F.coalesce(
                F.get_json_object(
                    F.col("outputs"), "$.decisions.financial.NEEDS_REVIEW"
                ).cast("int"),
                F.lit(0),
            ).alias("needs_review_count"),
        )

        # Combine old and new format results
        old_selected = old_result.select(
            "metadata_merchant_name",
            "label_type",
            "valid_count",
            "invalid_count",
            "needs_review_count",
        )

        result = (
            old_selected.unionByName(unified_currency)
            .unionByName(unified_metadata)
            .unionByName(unified_financial)
        )

        # Aggregate by merchant and label type
        aggregated = result.groupBy(
            "metadata_merchant_name", "label_type"
        ).agg(
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

        logger.info("Computed decision analysis")
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

        logger.info("Computed token usage")
        return result

    def extract_langgraph_receipts(self, df: DataFrame) -> list[dict]:
        """Extract LangGraph trace outputs for visualization cache.

        Filters to name='LangGraph' rows and extracts receipt data from
        outputs.
        Returns collected data for driver-side processing.

        This method is used by viz_cache_job to extract receipt data from
        LangSmith Parquet exports for building visualization cache files.

        Args:
            df: DataFrame with raw trace data (from read_parquet).

        Returns:
            List of dicts containing outputs from receipt evaluation traces.
            Each dict has an 'outputs' key with the raw JSON string.
        """
        logger.info("Extracting receipt evaluation traces from DataFrame")
        # Support both old format (LangGraph) and new format
        # (ReceiptEvaluation)
        receipt_df = df.filter(
            F.col("name").isin(["LangGraph", "ReceiptEvaluation"])
        ).select("outputs")

        # Collect to driver for processing
        rows = receipt_df.collect()
        result = [row.asDict() for row in rows]

        logger.info("Extracted %d receipt evaluation traces", len(result))
        return result

    def read_receipt_data_files(
        self, batch_bucket: str, execution_id: str
    ) -> DataFrame:
        """Read receipt data files (words, labels, place) from S3 JSON files.

        Reads from: s3://{batch_bucket}/data/{execution_id}/*.json
        Each file contains: image_id, receipt_id, words, labels, place

        This is an alternative to reading from LangSmith Parquet exports,
        reading directly from the receipt data files stored during evaluation.

        Args:
            batch_bucket: S3 bucket containing batch files.
            execution_id: Execution ID to filter files.

        Returns:
            DataFrame with receipt data (image_id, receipt_id, words, labels,
            place).
        """
        path = f"s3a://{batch_bucket}/data/{execution_id}/"
        logger.info("Reading receipt data from: %s", path)

        df = self.spark.read.json(path)
        logger.info("Read %d receipt data files", df.count())
        return df

    def read_unified_results(
        self, batch_bucket: str, execution_id: str
    ) -> DataFrame:
        """Read unified evaluation results from S3 JSON files.

        Reads from: s3://{batch_bucket}/unified/{execution_id}/*.json
        Each file contains: image_id, receipt_id, merchant_name, decisions,
        etc.

        These files contain the merged results from all evaluators
        (currency, metadata, financial, geometric).

        Args:
            batch_bucket: S3 bucket containing batch files.
            execution_id: Execution ID to filter files.

        Returns:
            DataFrame with unified results.
        """
        path = f"s3a://{batch_bucket}/unified/{execution_id}/"
        logger.info("Reading unified results from: %s", path)

        df = self.spark.read.json(path)
        logger.info("Read %d unified result files", df.count())
        return df
