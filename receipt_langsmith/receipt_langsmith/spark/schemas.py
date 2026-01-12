"""Spark SQL schemas for LangSmith trace data.

This module defines PySpark StructType schemas matching the
LangSmith Parquet export format.
"""

from pyspark.sql.types import (
    BooleanType,
    DoubleType,
    IntegerType,
    LongType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

# Raw Parquet schema matching LangSmith bulk export format
LANGSMITH_PARQUET_SCHEMA = StructType(
    [
        # Trace hierarchy
        StructField("id", StringType(), False),
        StructField("trace_id", StringType(), False),
        StructField("parent_run_id", StringType(), True),
        StructField("dotted_order", StringType(), True),
        StructField("is_root", BooleanType(), True),
        StructField("parent_run_ids", StringType(), True),  # JSON array
        # Execution info
        StructField("name", StringType(), False),
        StructField("run_type", StringType(), False),
        StructField("status", StringType(), False),
        StructField("error", StringType(), True),
        StructField("start_time", TimestampType(), True),
        StructField("end_time", TimestampType(), True),
        StructField("first_token_time", TimestampType(), True),
        StructField("trace_tier", StringType(), True),
        # Token usage
        StructField("total_tokens", LongType(), True),
        StructField("prompt_tokens", LongType(), True),
        StructField("completion_tokens", LongType(), True),
        StructField("total_cost", DoubleType(), True),
        StructField("prompt_cost", DoubleType(), True),
        StructField("completion_cost", DoubleType(), True),
        # JSON string fields
        StructField("inputs", StringType(), True),
        StructField("outputs", StringType(), True),
        StructField("extra", StringType(), True),
        StructField("events", StringType(), True),
        StructField("tags", StringType(), True),
        # Metadata
        StructField("feedback_stats", StringType(), True),
        StructField("reference_example_id", StringType(), True),
        StructField("session_id", StringType(), True),
        StructField("tenant_id", StringType(), True),
        StructField("latency", DoubleType(), True),
    ]
)


# Analytics output schemas

RECEIPT_ANALYTICS_SCHEMA = StructType(
    [
        StructField("merchant_name", StringType(), True),
        StructField("image_id", StringType(), True),
        StructField("receipt_id", IntegerType(), True),
        StructField("execution_id", StringType(), True),
        StructField("total_duration_ms", LongType(), True),
        StructField("total_tokens", LongType(), True),
        StructField("prompt_tokens", LongType(), True),
        StructField("completion_tokens", LongType(), True),
        StructField("run_count", IntegerType(), True),
        StructField("llm_run_count", IntegerType(), True),
        StructField("status", StringType(), True),
    ]
)


STEP_TIMING_SCHEMA = StructType(
    [
        StructField("step_name", StringType(), False),
        StructField("avg_duration_ms", DoubleType(), True),
        StructField("p50_duration_ms", DoubleType(), True),
        StructField("p95_duration_ms", DoubleType(), True),
        StructField("p99_duration_ms", DoubleType(), True),
        StructField("min_duration_ms", LongType(), True),
        StructField("max_duration_ms", LongType(), True),
        StructField("total_runs", LongType(), True),
        StructField("total_tokens", LongType(), True),
    ]
)


DECISION_ANALYSIS_SCHEMA = StructType(
    [
        StructField("merchant_name", StringType(), True),
        StructField("label_type", StringType(), True),
        StructField("decision", StringType(), True),
        StructField("count", LongType(), True),
    ]
)


TOKEN_USAGE_SCHEMA = StructType(
    [
        StructField("date", StringType(), True),
        StructField("merchant_name", StringType(), True),
        StructField("total_tokens", LongType(), True),
        StructField("prompt_tokens", LongType(), True),
        StructField("completion_tokens", LongType(), True),
        StructField("trace_count", LongType(), True),
        StructField("estimated_cost_usd", DoubleType(), True),
    ]
)


# ============================================================================
# Label Validation Analytics Schemas (receipt-label-validation project)
# ============================================================================

LABEL_VALIDATION_RECEIPT_SCHEMA = StructType(
    [
        StructField("image_id", StringType(), True),
        StructField("receipt_id", IntegerType(), True),
        StructField("merchant_name", StringType(), True),
        StructField("execution_id", StringType(), True),
        # Timing breakdown
        StructField("total_duration_ms", LongType(), True),
        StructField("s3_download_duration_ms", LongType(), True),
        StructField("embedding_duration_ms", LongType(), True),
        StructField("chroma_validation_duration_ms", LongType(), True),
        StructField("llm_validation_duration_ms", LongType(), True),
        StructField("merchant_resolution_duration_ms", LongType(), True),
        # Counts
        StructField("words_count", IntegerType(), True),
        StructField("lines_count", IntegerType(), True),
        StructField("labels_validated", IntegerType(), True),
        StructField("labels_corrected", IntegerType(), True),
        StructField("chroma_validated", IntegerType(), True),
        StructField("llm_validated", IntegerType(), True),
        # Merchant resolution
        StructField("merchant_found", BooleanType(), True),
        StructField("merchant_resolution_tier", StringType(), True),
        StructField("merchant_confidence", DoubleType(), True),
        # Token usage
        StructField("total_tokens", LongType(), True),
        StructField("prompt_tokens", LongType(), True),
        StructField("completion_tokens", LongType(), True),
        StructField("embedding_tokens", LongType(), True),
        StructField("status", StringType(), True),
    ]
)


LABEL_VALIDATION_STEP_TIMING_SCHEMA = StructType(
    [
        StructField("step_name", StringType(), False),
        StructField("step_type", StringType(), True),  # s3, embedding, chroma, llm, merchant
        StructField("avg_duration_ms", DoubleType(), True),
        StructField("p50_duration_ms", DoubleType(), True),
        StructField("p95_duration_ms", DoubleType(), True),
        StructField("p99_duration_ms", DoubleType(), True),
        StructField("min_duration_ms", LongType(), True),
        StructField("max_duration_ms", LongType(), True),
        StructField("total_runs", LongType(), True),
        StructField("total_tokens", LongType(), True),
    ]
)


LABEL_VALIDATION_DECISION_SCHEMA = StructType(
    [
        StructField("validation_source", StringType(), True),  # chroma, llm
        StructField("decision", StringType(), True),  # valid, invalid, needs_review
        StructField("label_type", StringType(), True),  # predicted_label value
        StructField("count", LongType(), True),
        StructField("avg_confidence", DoubleType(), True),
    ]
)


MERCHANT_RESOLUTION_SCHEMA = StructType(
    [
        StructField("resolution_tier", StringType(), True),  # phone, address, text
        StructField("success_count", LongType(), True),
        StructField("failure_count", LongType(), True),
        StructField("success_rate", DoubleType(), True),
        StructField("avg_confidence", DoubleType(), True),
        StructField("total_attempts", LongType(), True),
    ]
)
