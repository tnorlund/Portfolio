"""Parsing utilities for LangSmith exports.

This module provides:
- TraceTreeBuilder: Reconstruct parent-child trace hierarchy
- TraceIndex: Efficient parent-child trace mapping
- JSON parsing utilities
- Trace helper functions
"""

from receipt_langsmith.parsers.json_fields import parse_extra, parse_json
from receipt_langsmith.parsers.parquet import ParquetReader
# Label validation helpers (receipt-label-validation project)
from receipt_langsmith.parsers.trace_helpers import (
    LabelValidationTraceIndex,
    TraceIndex,
    build_evaluator_result,
    build_geometric_from_trace,
    build_geometric_result,
    build_label_validation_summary,
    build_merchant_resolution_summary,
    build_receipt_identifier,
    count_decisions,
    count_label_validation_decisions,
    extract_metadata,
    get_decisions_from_trace,
    get_duration_seconds,
    get_merchant_resolution_result,
    get_relative_timing,
    get_step_timings,
    is_all_needs_review,
    load_s3_result,
    parse_datetime,
)
from receipt_langsmith.parsers.trace_tree import TraceTreeBuilder

__all__ = [
    "ParquetReader",
    "TraceTreeBuilder",
    "TraceIndex",
    "parse_json",
    "parse_extra",
    # Trace helpers
    "extract_metadata",
    "build_receipt_identifier",
    "count_decisions",
    "is_all_needs_review",
    "parse_datetime",
    "get_duration_seconds",
    "get_relative_timing",
    "load_s3_result",
    "build_evaluator_result",
    "build_geometric_result",
    "build_geometric_from_trace",
    "get_decisions_from_trace",
    # Label validation helpers (receipt-label-validation project)
    "LabelValidationTraceIndex",
    "build_label_validation_summary",
    "build_merchant_resolution_summary",
    "count_label_validation_decisions",
    "get_merchant_resolution_result",
    "get_step_timings",
]
