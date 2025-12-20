"""
Factory functions for creating properly configured clients.

This module provides factory functions that create clients with
the correct configuration for caching and performance optimization.
"""

import logging
from typing import Any, Callable, Optional

from receipt_agent.config.settings import Settings, get_settings

logger = logging.getLogger(__name__)


def create_dynamo_client(
    table_name: Optional[str] = None,
    settings: Optional[Settings] = None,
) -> Any:
    """
    Create a DynamoDB client for receipt operations.

    The client provides access to:
    - Receipt place data (ReceiptPlace)
    - Receipt details (lines, words, labels)
    - Places cache (PlacesCache) for cost optimization

    Args:
        table_name: DynamoDB table name (defaults to settings)
        settings: Configuration settings

    Returns:
        DynamoClient instance from receipt_dynamo
    """
    if settings is None:
        settings = get_settings()

    table = table_name or settings.dynamo_table_name

    try:
        from receipt_dynamo.data.dynamo_client import DynamoClient

        client = DynamoClient(table_name=table)
        logger.info(f"Created DynamoDB client for table: {table}")
        return client

    except ImportError as e:
        logger.error(
            "Failed to import receipt_dynamo. "
            "Install with: pip install receipt_dynamo"
        )
        raise ImportError(
            "receipt_dynamo package required for DynamoDB operations"
        ) from e


def create_chroma_client(
    persist_directory: Optional[str] = None,
    http_url: Optional[str] = None,
    mode: str = "read",
    settings: Optional[Settings] = None,
) -> Any:
    """
    Create a ChromaDB client for similarity search.

    The client provides access to:
    - Line embeddings (collection: "lines")
    - Word embeddings (collection: "words")

    If RECEIPT_AGENT_CHROMA_LINES_DIRECTORY and
    RECEIPT_AGENT_CHROMA_WORDS_DIRECTORY are set, creates separate clients
    for each collection and returns a DualChromaClient.

    Args:
        persist_directory: Local path to ChromaDB (defaults to settings)
        http_url: HTTP URL for remote ChromaDB (defaults to settings)
        mode: Operation mode ("read", "write", "delta")
        settings: Configuration settings

    Returns:
        ChromaClient instance from receipt_chroma, or DualChromaClient if
        separate directories are set
    """
    import os

    if settings is None:
        settings = get_settings()

    # Check for separate lines/words directories first (new approach)
    lines_dir = os.environ.get("RECEIPT_AGENT_CHROMA_LINES_DIRECTORY")
    words_dir = os.environ.get("RECEIPT_AGENT_CHROMA_WORDS_DIRECTORY")

    if lines_dir and words_dir:
        # Create separate clients for lines and words
        try:
            # Try direct import first, fallback to package import
            try:
                from receipt_chroma.data.chroma_client import ChromaClient
            except ImportError:
                from receipt_chroma import ChromaClient

            lines_client = ChromaClient(persist_directory=lines_dir, mode=mode)
            words_client = ChromaClient(persist_directory=words_dir, mode=mode)

            # Create DualChromaClient wrapper
            class DualChromaClient:
                """Routes queries to separate line and word clients."""

                def __init__(self, lines_client, words_client):
                    self.lines_client = lines_client
                    self.words_client = words_client

                def query(self, collection_name, **kwargs):
                    """Route query based on collection_name."""
                    if collection_name == "lines":
                        return self.lines_client.query(
                            collection_name="lines",
                            **kwargs,
                        )
                    elif collection_name == "words":
                        return self.words_client.query(
                            collection_name="words",
                            **kwargs,
                        )
                    else:
                        raise ValueError(
                            f"Unknown collection: {collection_name}"
                        )

                def get(self, collection_name, **kwargs):
                    """Route get based on collection_name."""
                    if collection_name == "lines":
                        return self.lines_client.get(
                            collection_name="lines",
                            **kwargs,
                        )
                    elif collection_name == "words":
                        return self.words_client.get(
                            collection_name="words",
                            **kwargs,
                        )
                    else:
                        raise ValueError(
                            f"Unknown collection: {collection_name}"
                        )

                def list_collections(self):
                    """Return both collections."""
                    return ["lines", "words"]

                def get_collection(self, collection_name, **kwargs):
                    """Route get_collection based on collection_name."""
                    if collection_name == "lines":
                        return self.lines_client.get_collection(
                            "lines",
                            **kwargs,
                        )
                    elif collection_name == "words":
                        return self.words_client.get_collection(
                            "words",
                            **kwargs,
                        )
                    else:
                        raise ValueError(
                            f"Unknown collection: {collection_name}"
                        )

                def __enter__(self):
                    if hasattr(self.lines_client, "__enter__"):
                        self.lines_client.__enter__()
                    if hasattr(self.words_client, "__enter__"):
                        self.words_client.__enter__()
                    return self

                def __exit__(self, exc_type, exc_val, exc_tb):
                    if hasattr(self.lines_client, "__exit__"):
                        self.lines_client.__exit__(exc_type, exc_val, exc_tb)
                    if hasattr(self.words_client, "__exit__"):
                        self.words_client.__exit__(exc_type, exc_val, exc_tb)

            client = DualChromaClient(lines_client, words_client)
            logger.info(f"Created ChromaDB client at: {lines_dir}")
            logger.info(f"Created ChromaDB client at: {words_dir}")
            return client

        except ImportError as e:
            logger.error(
                "Failed to import receipt_chroma. "
                "Install with: pip install receipt_chroma"
            )
            raise ImportError(
                "receipt_chroma package required for ChromaDB operations"
            ) from e

    # Fall back to single client (legacy approach)
    persist_dir = persist_directory or settings.chroma_persist_directory
    url = http_url or settings.chroma_http_url

    try:
        # Try direct import first, fallback to package import
        try:
            from receipt_chroma.data.chroma_client import ChromaClient
        except ImportError:
            from receipt_chroma import ChromaClient

        if url:
            client = ChromaClient(http_url=url, mode=mode)
            logger.info(f"Created ChromaDB HTTP client: {url}")
        elif persist_dir:
            client = ChromaClient(persist_directory=persist_dir, mode=mode)
            logger.info(f"Created ChromaDB client at: {persist_dir}")
        else:
            raise ValueError(
                "Either persist_directory or http_url must be specified. "
                "Set RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY, "
                "RECEIPT_AGENT_CHROMA_HTTP_URL, or "
                "RECEIPT_AGENT_CHROMA_LINES_DIRECTORY + "
                "RECEIPT_AGENT_CHROMA_WORDS_DIRECTORY"
            )

        return client

    except ImportError as e:
        logger.error(
            "Failed to import receipt_chroma. "
            "Install with: pip install receipt_chroma"
        )
        raise ImportError(
            "receipt_chroma package required for ChromaDB operations"
        ) from e


def create_places_client(
    api_key: Optional[str] = None,
    table_name: Optional[str] = None,
    settings: Optional[Settings] = None,
) -> Any:
    """
    Create a Google Places API client WITH DynamoDB caching.

    Uses the standalone receipt_places package which provides:
    - Automatic DynamoDB cache lookup before API calls
    - Cache responses for configurable TTL (default 30 days)
    - Query count tracking for analytics
    - Smart exclusions for area searches and route-level results

    The caching significantly reduces Places API costs:
    - Phone searches: 70-90% cache hit rate
    - Address searches: 40-60% cache hit rate

    Args:
        api_key: Google Places API key (defaults to settings)
        table_name: DynamoDB table name for caching (defaults to settings)
        settings: Configuration settings

    Returns:
        PlacesClient instance from receipt_places with caching enabled
    """
    if settings is None:
        settings = get_settings()

    key = api_key or settings.google_places_api_key.get_secret_value()

    if not key:
        logger.warning(
            "Google Places API key not configured. "
            "Set RECEIPT_AGENT_GOOGLE_PLACES_API_KEY"
        )
        return None

    table = table_name or settings.dynamo_table_name

    try:
        from receipt_places import PlacesClient, PlacesConfig

        # Create PlacesConfig with our settings
        places_config = PlacesConfig(
            api_key=key,
            table_name=table,
            cache_enabled=True,
            cache_ttl_days=30,
        )

        # PlacesClient includes built-in caching via CacheManager
        places_client = PlacesClient(config=places_config)

        logger.info(
            "Created PlacesClient with DynamoDB caching enabled (table=%s)",
            table,
        )
        return places_client

    except ImportError as e:
        logger.error(
            "Failed to import receipt_places. "
            "Install with: pip install receipt_places"
        )
        raise ImportError(
            "receipt_places package required for Places API operations"
        ) from e


# Backward compatibility alias
create_places_api = create_places_client


def create_embed_fn(
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    settings: Optional[Settings] = None,
) -> Callable[[list[str]], list[list[float]]]:
    """
    Create an embedding function for ChromaDB queries.

    Uses OpenAI's embedding API by default.

    Args:
        model: Embedding model name (defaults to settings)
        api_key: OpenAI API key (defaults to settings)
        settings: Configuration settings

    Returns:
        Function that takes list of strings and returns list of embeddings
    """
    if settings is None:
        settings = get_settings()

    model_name = model or settings.embedding_model
    key = api_key or settings.openai_api_key.get_secret_value()

    if not key:
        raise ValueError(
            "OpenAI API key required for embeddings. "
            "Set RECEIPT_AGENT_OPENAI_API_KEY"
        )

    try:
        from openai import OpenAI

        client = OpenAI(api_key=key)

        def embed_fn(texts: list[str]) -> list[list[float]]:
            """Generate embeddings using OpenAI API."""
            if not texts:
                return []

            response = client.embeddings.create(
                input=texts,
                model=model_name,
            )
            return [d.embedding for d in response.data]

        logger.info(f"Created embedding function using model: {model_name}")
        return embed_fn

    except ImportError as e:
        logger.error(
            "Failed to import openai. Install with: pip install openai"
        )
        raise ImportError("openai package required for embeddings") from e


def create_all_clients(
    settings: Optional[Settings] = None,
) -> dict[str, Any]:
    """
    Create all clients needed for the validation agent.

    This is a convenience function that creates properly configured:
    - DynamoDB client
    - ChromaDB client
    - Places API (with caching)
    - Embedding function

    Args:
        settings: Configuration settings

    Returns:
        Dictionary with keys: dynamo_client, chroma_client, places_api,
        embed_fn
    """
    if settings is None:
        settings = get_settings()

    dynamo = create_dynamo_client(settings=settings)
    chroma = create_chroma_client(settings=settings)
    embed_fn = create_embed_fn(settings=settings)

    # Places client uses its own built-in DynamoDB caching
    places = create_places_client(settings=settings)

    return {
        "dynamo_client": dynamo,
        "chroma_client": chroma,
        "places_api": places,
        "embed_fn": embed_fn,
    }
