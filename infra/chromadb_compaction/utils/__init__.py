"""ChromaDB Compaction observability utilities.

This package provides structured logging, metrics, tracing, and monitoring
utilities for ChromaDB compaction operations.
"""

from .logging import get_logger, get_operation_logger, StructuredFormatter
from .metrics import CompactionMetrics, metrics
from .tracing import trace_compaction_operation, trace_lambda_handler
from .response import format_response
from .timeout_handler import (
    start_compaction_lambda_monitoring,
    stop_compaction_lambda_monitoring,
    with_compaction_timeout_protection,
    check_compaction_timeout,
    compaction_operation_with_timeout,
)

# Create aliases for function name compatibility with enhanced_compaction_handler
def trace_function(operation_name=None, collection=None):
    """Alias for trace_compaction_operation.""" 
    return trace_compaction_operation(operation_name or "compaction")

__all__ = [
    "get_logger",
    "get_operation_logger", 
    "StructuredFormatter",
    "CompactionMetrics",
    "metrics",
    "trace_compaction_operation",
    "trace_lambda_handler",
    "trace_function",  # Alias for compatibility
    "format_response",
    "start_compaction_lambda_monitoring",
    "stop_compaction_lambda_monitoring", 
    "with_compaction_timeout_protection",
    "check_compaction_timeout",
    "compaction_operation_with_timeout",
]