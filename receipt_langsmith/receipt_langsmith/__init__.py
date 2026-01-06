"""LangSmith query helpers for receipt evaluation visualization caches.

This package provides functions to query LangSmith traces for visualization
data, enabling real-time cache generation from trace outputs rather than
scanning S3 buckets.

Main functions (API-based):
- find_receipts_with_anomalies: Find receipts with geometric/constellation issues
- find_receipts_with_llm_decisions: Find receipts with LLM evaluation decisions

Parquet functions (bulk export):
- find_receipts_with_decisions_from_parquet: Read from exported Parquet files
- find_receipts_with_anomalies_from_parquet: Read anomalies from Parquet files
- read_traces_from_parquet: Low-level Parquet reader
"""

__version__ = "0.1.0"

from receipt_langsmith.parquet_reader import (
    find_receipts_with_anomalies_from_parquet,
    find_receipts_with_decisions_from_parquet,
    read_traces_from_parquet,
)
from receipt_langsmith.queries import (
    find_receipts_with_anomalies,
    find_receipts_with_llm_decisions,
    get_child_traces,
    get_langsmith_client,
    query_recent_receipt_traces,
)

__all__ = [
    "__version__",
    # API-based queries
    "get_langsmith_client",
    "query_recent_receipt_traces",
    "get_child_traces",
    "find_receipts_with_anomalies",
    "find_receipts_with_llm_decisions",
    # Parquet-based queries
    "read_traces_from_parquet",
    "find_receipts_with_decisions_from_parquet",
    "find_receipts_with_anomalies_from_parquet",
]
