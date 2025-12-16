"""
Thin wrapper around OpenAI embeddings API for realtime embedding requests.

Keeps the model selection consistent with batch submissions so realtime
embeddings match batch embeddings.
"""

from typing import List, Optional, Sequence

from openai import OpenAI


def embed_texts(
    client: OpenAI,
    texts: Sequence[str],
    model: str = "text-embedding-3-small",
    api_key: Optional[str] = None,
) -> List[List[float]]:
    """
    Embed a sequence of texts using OpenAI embeddings API.

    Args:
        client: OpenAI client instance
        texts: Sequence of input strings
        model: Embedding model name (default: text-embedding-3-small)
        api_key: Optional API key if a client is not provided

    Returns:
        List of embedding vectors (same order as inputs)
    """
    if not texts:
        return []
    if client is None:
        client = OpenAI(api_key=api_key)
    resp = client.embeddings.create(model=model, input=list(texts))
    return [item.embedding for item in resp.data]
