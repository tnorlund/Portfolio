"""Abstract base interface for vector store clients."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class VectorStoreInterface(ABC):
    """
    Abstract interface for vector store operations.
    
    This interface defines the contract that all vector store implementations
    must follow, enabling easy swapping between different backends (ChromaDB,
    Pinecone, Weaviate, etc.).
    """

    @abstractmethod
    def get_collection(
        self, 
        name: str, 
        create_if_missing: bool = False,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Any:
        """
        Get or create a collection.
        
        Args:
            name: Collection name
            create_if_missing: Whether to create if it doesn't exist
            metadata: Optional metadata for new collections
            
        Returns:
            Collection object (implementation-specific)
        """
        pass

    @abstractmethod
    def upsert_vectors(
        self,
        collection_name: str,
        ids: List[str],
        embeddings: Optional[List[List[float]]] = None,
        documents: Optional[List[str]] = None,
        metadatas: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """
        Upsert vectors into a collection.
        
        Args:
            collection_name: Name of the collection
            ids: List of unique IDs
            embeddings: Optional list of embedding vectors
            documents: Optional list of documents (for auto-embedding)
            metadatas: Optional list of metadata dictionaries
        """
        pass

    @abstractmethod
    def query(
        self,
        collection_name: str,
        query_embeddings: Optional[List[List[float]]] = None,
        query_texts: Optional[List[str]] = None,
        n_results: int = 10,
        where: Optional[Dict[str, Any]] = None,
        include: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Query vectors from a collection.
        
        Args:
            collection_name: Name of the collection
            query_embeddings: Optional query embedding vectors
            query_texts: Optional query texts (for auto-embedding)
            n_results: Number of results to return
            where: Optional filter conditions
            include: Fields to include in results
            
        Returns:
            Query results dictionary
        """
        pass

    @abstractmethod
    def get_by_ids(
        self,
        collection_name: str,
        ids: List[str],
        include: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Get vectors by their IDs.
        
        Args:
            collection_name: Name of the collection
            ids: List of IDs to retrieve
            include: Fields to include in results
            
        Returns:
            Results dictionary
        """
        pass

    @abstractmethod
    def delete(
        self,
        collection_name: str,
        ids: Optional[List[str]] = None,
        where: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Delete vectors from a collection.
        
        Args:
            collection_name: Name of the collection
            ids: Optional list of IDs to delete
            where: Optional filter for deletion
        """
        pass

    @abstractmethod
    def count(self, collection_name: str) -> int:
        """
        Get the number of items in a collection.
        
        Args:
            collection_name: Name of the collection
            
        Returns:
            Number of items in the collection
        """
        pass

    @abstractmethod
    def list_collections(self) -> List[str]:
        """
        List all available collections.
        
        Returns:
            List of collection names
        """
        pass

    @abstractmethod
    def collection_exists(self, name: str) -> bool:
        """
        Check if a collection exists.
        
        Args:
            name: Collection name to check
            
        Returns:
            True if collection exists, False otherwise
        """
        pass

    @abstractmethod
    def reset(self) -> None:
        """Reset the client and clear all collections (useful for testing)."""
        pass