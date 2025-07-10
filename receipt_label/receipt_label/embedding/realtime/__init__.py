"""Real-time embedding module for immediate receipt processing.

This module provides real-time embedding capabilities alongside the existing
batch processing system. It's designed for use cases requiring immediate
embeddings, such as merchant validation and user-facing features.
"""

from .embed import (
    EmbeddingContext,
    embed_receipt_realtime,
    embed_words_realtime,
)

__all__ = [
    "embed_receipt_realtime",
    "embed_words_realtime",
    "EmbeddingContext",
]
