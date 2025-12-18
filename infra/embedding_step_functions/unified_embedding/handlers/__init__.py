"""Handler modules for unified embedding Lambda functions.

Each handler module exports a 'handle' function that processes
Lambda events and returns pure data (no HTTP wrapping).
"""

# Import handler modules (not functions) for lazy loading
from . import (
    compaction,
    create_chunk_groups,
    find_unembedded,
    find_unembedded_words,
    line_polling,
    list_pending,
    mark_batches_complete,
    split_into_chunks,
    submit_openai,
    submit_words_openai,
    word_polling,
)

__all__ = [
    "word_polling",
    "line_polling",
    "compaction",
    "find_unembedded",
    "find_unembedded_words",
    "submit_openai",
    "submit_words_openai",
    "list_pending",
    "mark_batches_complete",
    "create_chunk_groups",
    "split_into_chunks",
]
