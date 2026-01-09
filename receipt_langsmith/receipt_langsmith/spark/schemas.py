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
        StructField("example_word_text", StringType(), True),
        StructField("example_reasoning", StringType(), True),
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
