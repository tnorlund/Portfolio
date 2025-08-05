import os
from typing import Optional, TYPE_CHECKING

from openai import OpenAI
from receipt_dynamo import DynamoClient

from .client_manager import ClientConfig, ClientManager

# Only import Pinecone for type checking, not at runtime
if TYPE_CHECKING:
    from pinecone import Pinecone

# Global client manager instance (lazy initialized)
_default_manager: Optional[ClientManager] = None


def get_client_manager() -> ClientManager:
    """
    Get the default client manager instance.

    This creates a singleton ClientManager using environment variables.
    For testing, you should create your own ClientManager instance instead.

    Returns:
        The default ClientManager instance
    """
    global _default_manager
    if _default_manager is None:
        _default_manager = ClientManager(ClientConfig.from_env())
    return _default_manager


def get_clients():
    """
    Get client instances for DynamoDB, OpenAI, and Pinecone.

    DEPRECATED: This function is maintained for backward compatibility.
    New code should use ClientManager directly via get_client_manager()
    or by injecting a ClientManager instance.

    Returns:
        Tuple of (dynamo_client, openai_client, pinecone_index)
    """
    manager = get_client_manager()
    return manager.get_all_clients()
