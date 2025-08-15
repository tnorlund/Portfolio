"""
Common utilities for embedding batch processing.

This module contains shared functionality for handling OpenAI batch operations
across both word and line embedding pipelines.
"""

from receipt_label.embedding.common.batch_status_handler import (
    handle_batch_status,
    handle_cancelled_status,
    handle_completed_status,
    handle_expired_status,
    handle_failed_status,
    handle_in_progress_status,
    map_openai_to_dynamo_status,
    mark_items_for_retry,
    process_error_file,
    process_partial_results,
    should_retry_batch,
)

__all__ = [
    "handle_batch_status",
    "handle_completed_status",
    "handle_failed_status",
    "handle_expired_status",
    "handle_in_progress_status",
    "handle_cancelled_status",
    "map_openai_to_dynamo_status",
    "process_error_file",
    "process_partial_results",
    "mark_items_for_retry",
    "should_retry_batch",
]