"""OpenAI batch API orchestration module.

This module provides functionality for submitting, polling, and handling
OpenAI batch embedding jobs.
"""

from receipt_chroma.embedding.openai.batch_status import (
    handle_batch_status,
    map_openai_to_dynamo_status,
    mark_items_for_retry,
    process_error_file,
    process_partial_results,
    should_retry_batch,
)
from receipt_chroma.embedding.openai.helpers import (
    get_unique_receipt_and_image_ids,
)
from receipt_chroma.embedding.openai.poll import (
    download_openai_batch_result,
    get_openai_batch_status,
    list_pending_line_embedding_batches,
    list_pending_word_embedding_batches,
)
from receipt_chroma.embedding.openai.realtime import embed_texts
from receipt_chroma.embedding.openai.submit import (
    add_batch_summary,
    create_batch_summary,
    submit_openai_batch,
    upload_to_openai,
)

__all__ = [
    "handle_batch_status",
    "mark_items_for_retry",
    "map_openai_to_dynamo_status",
    "process_error_file",
    "process_partial_results",
    "should_retry_batch",
    "get_unique_receipt_and_image_ids",
    "download_openai_batch_result",
    "get_openai_batch_status",
    "list_pending_line_embedding_batches",
    "list_pending_word_embedding_batches",
    "add_batch_summary",
    "create_batch_summary",
    "submit_openai_batch",
    "upload_to_openai",
    "embed_texts",
]
