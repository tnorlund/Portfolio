"""
Shared connector tools for receipt_agent.

Agent-specific tools live under `agents/<name>/tools`.
This package only exposes shared connectors (chroma, dynamo, places)
and the tool registry helper.
"""

from receipt_agent.tools.chroma import (
    query_similar_lines,
    query_similar_words,
    search_by_merchant_name,
    search_by_place_id,
)
from receipt_agent.tools.dynamo import (
    get_receipt_context,
    get_receipt_place,
    get_receipts_by_merchant,
)
from receipt_agent.tools.places import verify_with_google_places
from receipt_agent.tools.registry import create_tool_registry

__all__ = [
    # ChromaDB tools
    "query_similar_lines",
    "query_similar_words",
    "search_by_merchant_name",
    "search_by_place_id",
    # DynamoDB tools
    "get_receipt_context",
    "get_receipt_place",
    "get_receipts_by_merchant",
    # Places tools
    "verify_with_google_places",
    # Registry
    "create_tool_registry",
]
