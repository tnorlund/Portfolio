"""
Tool registry for binding runtime dependencies to agent tools.

This module provides a registry pattern that allows tools to be
configured with actual client instances at runtime, making them
testable and reusable.
"""

import logging
from functools import partial
from typing import Any, Callable, Optional

from langchain_core.tools import BaseTool

from receipt_agent.tools.chroma import (
    query_similar_lines,
    query_similar_words,
    search_by_merchant_name,
    search_by_place_id,
)
from receipt_agent.tools.dynamo import (
    get_receipt_context,
    get_receipt_metadata,
    get_receipts_by_merchant,
)
from receipt_agent.tools.places import (
    compare_metadata_with_places,
    verify_with_google_places,
)

logger = logging.getLogger(__name__)


class ToolRegistry:
    """
    Registry for agent tools with runtime dependency injection.

    This class manages the binding of external clients (ChromaDB, DynamoDB,
    Google Places) to the agent tools, allowing for:
    - Clean separation of tool definitions from client configuration
    - Easy testing with mock clients
    - Runtime configuration of different environments
    """

    def __init__(
        self,
        chroma_client: Optional[Any] = None,
        dynamo_client: Optional[Any] = None,
        places_api: Optional[Any] = None,
        embed_fn: Optional[Callable[[list[str]], list[list[float]]]] = None,
    ):
        """
        Initialize the tool registry with client dependencies.

        Args:
            chroma_client: ChromaDB client for similarity search
            dynamo_client: DynamoDB client for receipt data
            places_api: Google Places API client
            embed_fn: Function to generate embeddings from text
        """
        self._chroma_client = chroma_client
        self._dynamo_client = dynamo_client
        self._places_api = places_api
        self._embed_fn = embed_fn
        self._bound_tools: list[BaseTool] = []

    def _bind_tool(
        self,
        tool_fn: BaseTool,
        **kwargs: Any,
    ) -> BaseTool:
        """
        Bind runtime dependencies to a tool function.

        Creates a new tool with the dependencies injected via partial application.
        """
        # Get the underlying function
        if hasattr(tool_fn, "func"):
            func = tool_fn.func
        else:
            func = tool_fn

        # Create partial with injected dependencies
        bound_func = partial(func, **kwargs)

        # Create new tool with bound function
        # We need to preserve the tool's metadata while using the bound function
        from langchain_core.tools import StructuredTool

        return StructuredTool(
            name=tool_fn.name,
            description=tool_fn.description,
            func=bound_func,
            args_schema=tool_fn.args_schema,
        )

    def get_chroma_tools(self) -> list[BaseTool]:
        """Get ChromaDB tools with bound client."""
        tools = []

        if self._chroma_client is not None:
            tools.extend([
                self._bind_tool(
                    query_similar_lines,
                    _chroma_client=self._chroma_client,
                    _embed_fn=self._embed_fn,
                ),
                self._bind_tool(
                    query_similar_words,
                    _chroma_client=self._chroma_client,
                    _embed_fn=self._embed_fn,
                ),
                self._bind_tool(
                    search_by_merchant_name,
                    _chroma_client=self._chroma_client,
                    _embed_fn=self._embed_fn,
                ),
                self._bind_tool(
                    search_by_place_id,
                    _chroma_client=self._chroma_client,
                ),
            ])
        else:
            logger.warning("ChromaDB client not configured - chroma tools disabled")

        return tools

    def get_dynamo_tools(self) -> list[BaseTool]:
        """Get DynamoDB tools with bound client."""
        tools = []

        if self._dynamo_client is not None:
            tools.extend([
                self._bind_tool(
                    get_receipt_metadata,
                    _dynamo_client=self._dynamo_client,
                ),
                self._bind_tool(
                    get_receipt_context,
                    _dynamo_client=self._dynamo_client,
                ),
                self._bind_tool(
                    get_receipts_by_merchant,
                    _dynamo_client=self._dynamo_client,
                ),
            ])
        else:
            logger.warning("DynamoDB client not configured - dynamo tools disabled")

        return tools

    def get_places_tools(self) -> list[BaseTool]:
        """Get Google Places tools with bound client."""
        tools = []

        if self._places_api is not None:
            tools.append(
                self._bind_tool(
                    verify_with_google_places,
                    _places_api=self._places_api,
                )
            )
        else:
            logger.warning("Places API not configured - places tools disabled")

        # compare_metadata_with_places doesn't need external deps
        tools.append(compare_metadata_with_places)

        return tools

    def get_all_tools(self) -> list[BaseTool]:
        """Get all tools with bound dependencies."""
        tools = []
        tools.extend(self.get_chroma_tools())
        tools.extend(self.get_dynamo_tools())
        tools.extend(self.get_places_tools())
        return tools

    def get_tool_descriptions(self) -> str:
        """Get formatted descriptions of all available tools."""
        tools = self.get_all_tools()
        descriptions = []
        for tool in tools:
            descriptions.append(f"- **{tool.name}**: {tool.description}")
        return "\n".join(descriptions)


def create_tool_registry(
    chroma_client: Optional[Any] = None,
    dynamo_client: Optional[Any] = None,
    places_api: Optional[Any] = None,
    embed_fn: Optional[Callable[[list[str]], list[list[float]]]] = None,
) -> ToolRegistry:
    """
    Factory function to create a configured ToolRegistry.

    Args:
        chroma_client: ChromaDB client instance
        dynamo_client: DynamoDB client instance
        places_api: Google Places API client
        embed_fn: Embedding function for ChromaDB queries

    Returns:
        Configured ToolRegistry instance
    """
    return ToolRegistry(
        chroma_client=chroma_client,
        dynamo_client=dynamo_client,
        places_api=places_api,
        embed_fn=embed_fn,
    )

