"""
Refactored ChromaDB client utilities for receipt label system.

This module provides a simplified interface to ChromaDB with support for:
- Separate databases for different collection types (words, lines, etc.)
- Read-only and read-write modes
- Direct collection access without prefix management
"""

import logging
import os
from typing import Any, Dict, List, Optional

try:
    import chromadb
    from chromadb import Collection
    from chromadb.config import Settings
    from chromadb.utils import embedding_functions

    CHROMADB_AVAILABLE = True
except ImportError:
    CHROMADB_AVAILABLE = False
    Collection = Any

logger = logging.getLogger(__name__)


class ChromaDBClient:
    """
    Manages ChromaDB client instances for separate collection databases.
    
    Since each collection type (words, lines, etc.) is stored as a separate
    database in S3, this client provides direct access without prefix management.
    """

    def __init__(
        self,
        persist_directory: Optional[str] = None,
        mode: str = "read",  # "read" | "write"
        embedding_function: Optional[Any] = None,
    ):
        """
        Initialize the ChromaDB client manager.

        Args:
            persist_directory: Directory for local ChromaDB persistence.
                             If None, uses in-memory storage.
            mode: Operation mode - "read" (read-only) or "write" (read-write)
            embedding_function: Optional custom embedding function. 
                               If None and mode="write", uses OpenAI by default.
        """
        if not CHROMADB_AVAILABLE:
            raise RuntimeError(
                "ChromaDB is not available. Install chromadb with: pip install chromadb"
            )

        self.persist_directory = persist_directory
        self.mode = mode.lower()
        self.use_persistent_client = persist_directory is not None
        self._client: Optional[chromadb.Client] = None
        self._collections: Dict[str, Collection] = {}
        
        # Only set embedding function if in write mode
        if mode == "write" and embedding_function is None:
            # Default to OpenAI for backward compatibility
            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                raise RuntimeError(
                    "OPENAI_API_KEY must be set when using default OpenAI embeddings in write mode"
                )
            self._embedding_function = embedding_functions.OpenAIEmbeddingFunction(
                api_key=api_key,
                model_name="text-embedding-3-small",
            )
        else:
            self._embedding_function = embedding_function

    @property
    def client(self) -> chromadb.Client:
        """Get or create ChromaDB client."""
        if self._client is None:
            if self.use_persistent_client and self.persist_directory:
                logger.debug(f"Creating persistent client at: {self.persist_directory}")
                self._client = chromadb.PersistentClient(
                    path=self.persist_directory,
                    settings=Settings(
                        anonymized_telemetry=False,
                        allow_reset=self.mode == "write",
                    )
                )
            else:
                logger.debug("Creating in-memory client")
                self._client = chromadb.Client(
                    Settings(anonymized_telemetry=False)
                )
        return self._client

    def get_collection(
        self, 
        name: str,
        create_if_missing: bool = False,
        metadata: Optional[Dict] = None
    ) -> Collection:
        """
        Get a collection by name.
        
        Args:
            name: The collection name (e.g., "receipt_words", "receipt_lines")
            create_if_missing: Whether to create the collection if it doesn't exist
            metadata: Optional metadata for new collections
            
        Returns:
            ChromaDB Collection instance
        """
        if name not in self._collections:
            try:
                # For read mode, don't specify embedding function
                if self.mode == "read":
                    self._collections[name] = self.client.get_collection(name=name)
                else:
                    # For write mode, include embedding function
                    self._collections[name] = self.client.get_collection(
                        name=name, 
                        embedding_function=self._embedding_function
                    )
                logger.debug(f"Retrieved existing collection: {name}")
                
            except Exception as e:
                if create_if_missing and self.mode == "write":
                    # Create new collection
                    self._collections[name] = self.client.create_collection(
                        name=name,
                        embedding_function=self._embedding_function,
                        metadata=metadata or {"description": f"Collection: {name}"},
                    )
                    logger.info(f"Created new collection: {name}")
                else:
                    raise ValueError(f"Collection '{name}' not found. Error: {e}") from e
        
        return self._collections[name]

    def list_collections(self) -> List[str]:
        """List all available collection names."""
        collections = self.client.list_collections()
        return [c.name for c in collections]

    def collection_exists(self, name: str) -> bool:
        """Check if a collection exists."""
        return name in self.list_collections()

    def _assert_writeable(self) -> None:
        """Ensure the client is in write mode."""
        if self.mode != "write":
            raise RuntimeError(f"This client is in {self.mode} mode, not write mode")

    def upsert_vectors(
        self,
        collection_name: str,
        ids: List[str],
        embeddings: List[List[float]],
        documents: Optional[List[str]] = None,
        metadatas: Optional[List[Dict]] = None,
    ) -> None:
        """
        Upsert vectors into a collection.
        
        Args:
            collection_name: Name of the collection
            ids: List of unique IDs
            embeddings: List of embedding vectors
            documents: Optional list of document strings
            metadatas: Optional list of metadata dicts
        """
        self._assert_writeable()
        
        collection = self.get_collection(collection_name, create_if_missing=True)
        
        # Prepare upsert arguments
        upsert_args = {
            "ids": ids,
            "embeddings": embeddings,
        }
        
        if documents:
            upsert_args["documents"] = documents
        if metadatas:
            upsert_args["metadatas"] = metadatas
            
        collection.upsert(**upsert_args)
        logger.debug(f"Upserted {len(ids)} vectors to {collection_name}")

    def query(
        self,
        collection_name: str,
        query_embeddings: Optional[List[List[float]]] = None,
        query_texts: Optional[List[str]] = None,
        n_results: int = 10,
        where: Optional[Dict] = None,
        include: Optional[List[str]] = None,
    ) -> Dict:
        """
        Query a collection for similar vectors.
        
        Args:
            collection_name: Name of the collection to query
            query_embeddings: Optional embedding vectors to search for
            query_texts: Optional text queries (requires embedding function)
            n_results: Number of results to return
            where: Optional filter conditions
            include: Fields to include in results
            
        Returns:
            Query results dictionary
        """
        collection = self.get_collection(collection_name)
        
        query_args = {"n_results": n_results}
        
        if query_embeddings:
            query_args["query_embeddings"] = query_embeddings
        elif query_texts:
            if self.mode == "read":
                raise ValueError("Text queries require write mode with embedding function")
            query_args["query_texts"] = query_texts
        else:
            raise ValueError("Either query_embeddings or query_texts must be provided")
            
        if where:
            query_args["where"] = where
        if include:
            query_args["include"] = include
            
        return collection.query(**query_args)

    def get_by_ids(
        self,
        collection_name: str,
        ids: List[str],
        include: Optional[List[str]] = None,
    ) -> Dict:
        """
        Get specific items by their IDs.
        
        Args:
            collection_name: Name of the collection
            ids: List of IDs to retrieve
            include: Fields to include in results
            
        Returns:
            Dictionary with retrieved items
        """
        collection = self.get_collection(collection_name)
        
        include = include or ["metadatas", "documents", "embeddings"]
        
        return collection.get(ids=ids, include=include)

    def delete(
        self,
        collection_name: str,
        ids: Optional[List[str]] = None,
        where: Optional[Dict] = None,
    ) -> None:
        """
        Delete items from a collection.
        
        Args:
            collection_name: Name of the collection
            ids: Optional list of IDs to delete
            where: Optional filter for items to delete
        """
        self._assert_writeable()
        
        collection = self.get_collection(collection_name)
        
        if ids:
            collection.delete(ids=ids)
            logger.debug(f"Deleted {len(ids)} items from {collection_name}")
        elif where:
            collection.delete(where=where)
            logger.debug(f"Deleted items matching filter from {collection_name}")
        else:
            raise ValueError("Either ids or where must be provided")

    def count(self, collection_name: str) -> int:
        """Get the count of items in a collection."""
        collection = self.get_collection(collection_name)
        return collection.count()


def get_word_client(persist_directory: str, mode: str = "read") -> ChromaDBClient:
    """
    Get a ChromaDB client for word embeddings.
    
    Args:
        persist_directory: Path to the word embeddings database
        mode: "read" or "write"
        
    Returns:
        ChromaDBClient instance configured for word embeddings
    """
    return ChromaDBClient(persist_directory=persist_directory, mode=mode)


def get_line_client(persist_directory: str, mode: str = "read") -> ChromaDBClient:
    """
    Get a ChromaDB client for line embeddings.
    
    Args:
        persist_directory: Path to the line embeddings database
        mode: "read" or "write"
        
    Returns:
        ChromaDBClient instance configured for line embeddings
    """
    return ChromaDBClient(persist_directory=persist_directory, mode=mode)


# Backward compatibility
def get_chroma_client(
    persist_directory: Optional[str] = None,
    collection_prefix: str = "",  # Ignored for backward compatibility
    mode: str = "read"
) -> ChromaDBClient:
    """
    Get a ChromaDB client (backward compatibility function).
    
    Args:
        persist_directory: Directory for ChromaDB persistence
        collection_prefix: DEPRECATED - no longer used
        mode: "read" or "write"
        
    Returns:
        ChromaDBClient instance
    """
    if collection_prefix:
        logger.warning(
            "collection_prefix parameter is deprecated and will be ignored. "
            "Collections are now accessed directly by name."
        )
    return ChromaDBClient(persist_directory=persist_directory, mode=mode)