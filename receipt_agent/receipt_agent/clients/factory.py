"""
Factory functions for creating properly configured clients.

This module provides factory functions that create clients with
the correct configuration for caching and performance optimization.
"""

import logging
from collections.abc import Callable
from typing import Any, Optional

from receipt_agent.config.settings import Settings, get_settings
from receipt_agent.exceptions import ReceiptAgentConfigurationError

try:
    from receipt_dynamo.data.dynamo_client import DynamoClient
except ImportError:  # pragma: no cover - optional dependency
    DynamoClient = None

try:
    from receipt_places import PlacesClient, PlacesConfig
except ImportError:  # pragma: no cover - optional dependency
    PlacesClient = None
    PlacesConfig = None

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - optional dependency
    OpenAI = None

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

    if DynamoClient is None:
        logger.error(
            "Failed to import receipt_dynamo. "
            "Install with: pip install receipt_dynamo"
        )
        raise ImportError(
            "receipt_dynamo package required for DynamoDB operations"
        )

    client = DynamoClient(table_name=table)
    logger.info("Created DynamoDB client for table: %s", table)
    return client


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
        if PlacesClient is None or PlacesConfig is None:
            raise ImportError(
                "receipt_places package required for Places API operations"
            )

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
    Create an embedding function for vector similarity queries.

    Uses OpenAI's embedding API by default.

    Args:
        model: Embedding model name (defaults to settings)
        api_key: OpenAI API key (defaults to settings)
        settings: Configuration settings

    Returns:
        Function that takes list of strings and returns list of embeddings

    Raises:
        ReceiptAgentConfigurationError: If the OpenAI API key is missing
    """
    if settings is None:
        settings = get_settings()

    model_name = model or settings.embedding_model
    key = api_key or settings.openai_api_key.get_secret_value()

    if not key:
        raise ReceiptAgentConfigurationError(
            "OpenAI API key required for embeddings. "
            "Set RECEIPT_AGENT_OPENAI_API_KEY"
        )

    try:
        if OpenAI is None:
            raise ImportError(
                "openai package required for embedding operations"
            )

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

        logger.info("Created embedding function using model: %s", model_name)
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
    - Places API (with caching)
    - Embedding function

    Args:
        settings: Configuration settings

    Returns:
        Dictionary with keys: dynamo_client, places_api, embed_fn
    """
    if settings is None:
        settings = get_settings()

    dynamo = create_dynamo_client(settings=settings)
    embed_fn = create_embed_fn(settings=settings)

    # Places client uses its own built-in DynamoDB caching
    places = create_places_client(settings=settings)

    return {
        "dynamo_client": dynamo,
        "places_api": places,
        "embed_fn": embed_fn,
    }
