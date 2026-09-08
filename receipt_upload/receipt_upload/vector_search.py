"""Backend selection for live-ingest vector-search consumers.

The selector implementation lives in ``receipt_embeddings.backend`` so
consumers that cannot depend on receipt_upload (receipt_agent's QA tools,
both MCP servers) share it; this module stays the receipt_upload import
surface with identical semantics.
"""

from __future__ import annotations

from receipt_embeddings.backend import vector_search_client

__all__ = ["vector_search_client"]
