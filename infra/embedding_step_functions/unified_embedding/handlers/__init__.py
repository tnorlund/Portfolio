"""Handler modules for unified embedding Lambda functions.

Each handler module exports a 'handle' function that processes
Lambda events and returns pure data (no HTTP wrapping).
"""

# Import handler modules (not functions) for lazy loading
from . import (
    compaction,
    find_unembedded,
    line_polling,
    list_pending,
    submit_openai,
    word_polling,
)

__all__ = [
    "word_polling",
    "line_polling",
    "compaction",
    "find_unembedded",
    "submit_openai",
    "list_pending",
]
