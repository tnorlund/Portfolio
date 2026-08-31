"""Back-compat re-export. Implementation lives in receipt_embeddings."""

from receipt_embeddings.openai.realtime import *  # noqa: F403
from receipt_embeddings.openai.realtime import embed_texts

__all__ = [
    "embed_texts",
]
