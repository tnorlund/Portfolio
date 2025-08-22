"""Shared utilities for unified embedding Lambda functions."""

from .response import format_response, is_step_function_invocation
from .logging import get_logger, get_operation_logger
from .aws_clients import get_s3_client, get_dynamodb_client, get_sqs_client
from .timeout_handler import (
    with_timeout_protection as with_compaction_timeout_protection,
    start_lambda_monitoring as start_compaction_lambda_monitoring,
    stop_lambda_monitoring as stop_compaction_lambda_monitoring,
)
from .tracing import (
    trace_chromadb_operation,
)
from . import metrics

# Create aliases for function name compatibility
def trace_function(operation_name=None, collection=None):
    """Alias for trace_chromadb_operation."""
    return trace_chromadb_operation(operation_name or "compaction")

def trace_compaction_operation(operation_name=None):
    """Alias for trace_chromadb_operation."""
    return trace_chromadb_operation(operation_name or "compaction")

__all__ = [
    "format_response",
    "is_step_function_invocation", 
    "get_logger",
    "get_operation_logger",
    "get_s3_client",
    "get_dynamodb_client",
    "get_sqs_client",
    "with_compaction_timeout_protection",
    "start_compaction_lambda_monitoring",
    "stop_compaction_lambda_monitoring",
    "trace_function",
    "trace_compaction_operation",
    "trace_chromadb_operation",
    "metrics",
]
