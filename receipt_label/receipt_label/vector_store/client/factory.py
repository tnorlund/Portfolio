"""
Vector client factory for creating vector store instances.

This factory provides a clean, consistent way to create vector store clients
while hiding implementation details and supporting different backends.
"""

import logging
import os
from typing import Optional, Any

from .base import VectorStoreInterface
from .chromadb_client import ChromaDBClient

logger = logging.getLogger(__name__)

# Singleton instance for backward compatibility
_default_client_instance: Optional[VectorStoreInterface] = None


class VectorClient:
    """
    Factory class for creating vector store clients.

    This class provides static methods for creating different types of
    vector store clients while maintaining a consistent interface.
    """

    @staticmethod
    def create_chromadb_client(
        persist_directory: Optional[str] = None,
        mode: str = "read",
        embedding_function: Optional[Any] = None,
        metadata_only: bool = False,
        http_url: Optional[str] = None,
    ) -> ChromaDBClient:
        """
        Create a ChromaDB client instance.

        Args:
            persist_directory: Directory for local ChromaDB persistence.
                             If None, uses in-memory storage.
            mode: Operation mode - "read", "write", "delta", or "snapshot"
            embedding_function: Optional custom embedding function
            metadata_only: If True, uses default embedding function to avoid API costs

        Returns:
            ChromaDBClient instance

        Raises:
            RuntimeError: If ChromaDB is not available
        """
        return ChromaDBClient(
            persist_directory=persist_directory,
            mode=mode,
            embedding_function=embedding_function,
            metadata_only=metadata_only,
            http_url=http_url,
        )

    @staticmethod
    def get_default_client(
        persist_directory: Optional[str] = None,
        reset: bool = False,
        mode: str = "read",
    ) -> VectorStoreInterface:
        """
        Get or create a singleton vector client instance.

        This method provides backward compatibility with the old get_chroma_client()
        function while using the new architecture.

        Args:
            persist_directory: Directory for persistence (uses env var if not provided)
            reset: Whether to reset the existing client
            mode: Operation mode for the client

        Returns:
            VectorStoreInterface instance (ChromaDB by default)
        """
        global _default_client_instance

        if reset or _default_client_instance is None:
            if persist_directory is None:
                persist_directory = os.environ.get("CHROMA_PERSIST_PATH")

            _default_client_instance = VectorClient.create_chromadb_client(
                persist_directory=persist_directory,
                mode=mode,
            )
            logger.debug("Created new default vector client (mode=%s)", mode)

        return _default_client_instance

    @staticmethod
    def create_word_client(
        persist_directory: str, mode: str = "read"
    ) -> ChromaDBClient:
        """
        Create a ChromaDB client specifically for word embeddings.

        This method provides backward compatibility with get_word_client().

        Args:
            persist_directory: Path to the word embeddings database
            mode: "read" or "write"

        Returns:
            ChromaDBClient instance configured for word embeddings
        """
        return VectorClient.create_chromadb_client(
            persist_directory=persist_directory,
            mode=mode,
        )

    @staticmethod
    def create_line_client(
        persist_directory: str, mode: str = "read"
    ) -> ChromaDBClient:
        """
        Create a ChromaDB client specifically for line embeddings.

        This method provides backward compatibility with get_line_client().

        Args:
            persist_directory: Path to the line embeddings database
            mode: "read" or "write"

        Returns:
            ChromaDBClient instance configured for line embeddings
        """
        return VectorClient.create_chromadb_client(
            persist_directory=persist_directory,
            mode=mode,
        )

    # Future extension points for other vector stores

    @staticmethod
    def create_pinecone_client(
        api_key: str, environment: str, **kwargs
    ) -> VectorStoreInterface:
        """
        Create a Pinecone client instance (placeholder for future implementation).

        Args:
            api_key: Pinecone API key
            environment: Pinecone environment
            **kwargs: Additional Pinecone configuration

        Returns:
            PineconeClient instance (when implemented)

        Raises:
            NotImplementedError: This is a placeholder for future implementation
        """
        raise NotImplementedError(
            "Pinecone client not yet implemented. Use create_chromadb_client() instead."
        )

    @staticmethod
    def create_weaviate_client(
        url: str, auth_config: Optional[dict] = None, **kwargs
    ) -> VectorStoreInterface:
        """
        Create a Weaviate client instance (placeholder for future implementation).

        Args:
            url: Weaviate instance URL
            auth_config: Authentication configuration
            **kwargs: Additional Weaviate configuration

        Returns:
            WeaviateClient instance (when implemented)

        Raises:
            NotImplementedError: This is a placeholder for future implementation
        """
        raise NotImplementedError(
            "Weaviate client not yet implemented. Use create_chromadb_client() instead."
        )


# Backward compatibility functions


def get_chroma_client(
    persist_directory: Optional[str] = None, reset: bool = False
) -> Optional[VectorStoreInterface]:
    """
    Legacy function for backward compatibility.

    This function maintains compatibility with existing code while using
    the new vector store architecture.

    Args:
        persist_directory: Directory for persistence (uses env var if not provided)
        reset: Whether to reset the existing client

    Returns:
        VectorStoreInterface instance or None if ChromaDB is not available
    """
    try:
        return VectorClient.get_default_client(
            persist_directory=persist_directory,
            reset=reset,
            mode="read",  # Default to read-only for backward compatibility
        )
    except RuntimeError:
        # ChromaDB not available
        return None


def get_word_client(
    persist_directory: str, mode: str = "read"
) -> ChromaDBClient:
    """Legacy function for backward compatibility with get_word_client()."""
    return VectorClient.create_word_client(persist_directory, mode)


def get_line_client(
    persist_directory: str, mode: str = "read"
) -> ChromaDBClient:
    """Legacy function for backward compatibility with get_line_client()."""
    return VectorClient.create_line_client(persist_directory, mode)
