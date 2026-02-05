"""Shared analytics helpers for Spark processors."""

from __future__ import annotations

from typing import Iterable

from pyspark.sql import Column, DataFrame
from pyspark.sql import functions as F


def build_trace_stats(
    df: DataFrame,
    *,
    include_llm_runs: bool = False,
    extra_aggs: Iterable[Column] | None = None,
) -> DataFrame:
    """Aggregate common trace-level stats by trace_id."""
    aggs: list[Column] = [
        F.sum("duration_ms").alias("total_duration_ms"),
        F.sum("total_tokens").alias("total_tokens"),
        F.sum("prompt_tokens").alias("prompt_tokens"),
        F.sum("completion_tokens").alias("completion_tokens"),
        F.count("*").alias("run_count"),
    ]
    if include_llm_runs:
        aggs.append(
            F.sum(F.when(F.col("run_type") == "llm", 1).otherwise(0)).alias(
                "llm_run_count"
            )
        )
    if extra_aggs:
        aggs.extend(extra_aggs)

    return df.groupBy("trace_id").agg(*aggs)


def duration_stats(
    df: DataFrame,
    *,
    group_cols: list[str],
    percentiles: Iterable[float] = (0.5, 0.95, 0.99),
    include_tokens: bool = True,
    count_alias: str = "total_runs",
) -> DataFrame:
    """Compute duration statistics for grouped traces."""
    aggs: list[Column] = [
        F.avg("duration_ms").alias("avg_duration_ms"),
    ]
    for percentile in percentiles:
        label = f"p{int(percentile * 100)}_duration_ms"
        aggs.append(
            F.expr(
                f"percentile_approx(duration_ms, {percentile})"
            ).alias(label)
        )
    aggs.extend(
        [
            F.min("duration_ms").alias("min_duration_ms"),
            F.max("duration_ms").alias("max_duration_ms"),
            F.count("*").alias(count_alias),
        ]
    )
    if include_tokens:
        aggs.append(F.sum("total_tokens").alias("total_tokens"))
    return df.groupBy(*group_cols).agg(*aggs)


def step_timing_stats(
    df: DataFrame,
    *,
    group_cols: list[str],
) -> DataFrame:
    """Compute duration statistics for steps."""
    return duration_stats(
        df,
        group_cols=group_cols,
        percentiles=(0.5, 0.95, 0.99),
        include_tokens=True,
        count_alias="total_runs",
    )
