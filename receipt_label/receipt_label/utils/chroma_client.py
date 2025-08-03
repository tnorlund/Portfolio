"""
ChromaDB client management module.

This module provides ChromaDB integration for vector storage,
replacing the previous Pinecone implementation.
"""

import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import chromadb
from chromadb import Collection
from chromadb.config import Settings
from chromadb.utils import embedding_functions


class ChromaDBClient:
    """
    Manages ChromaDB client instances with lazy initialization.

    This class provides centralized access to ChromaDB collections
    while supporting both local persistence and S3 sync capabilities.
    """

    def __init__(
        self,
        persist_directory: Optional[str] = None,
        collection_prefix: str = "receipts",
        mode: str = "read",  # "read" | "delta" | "snapshot"
    ):
        """
        Initialize the ChromaDB client manager.

        Args:
            persist_directory: Directory for local ChromaDB persistence.
                             If None, uses in-memory storage.
            collection_prefix: Prefix for collection names (e.g., "receipts_words")
            mode: Operation mode - "read" (read-only), "delta" (write deltas),
                  or "snapshot" (read-write snapshots)
        """
        self.persist_directory = persist_directory
        self.collection_prefix = collection_prefix
        self.mode = mode.lower()
        self.use_persistent_client = persist_directory is not None
        self._client: Optional[chromadb.Client] = None
        self._collections: Dict[str, Collection] = {}

        # OpenAI embedding function for consistency with current implementation
        self._embedding_function = embedding_functions.OpenAIEmbeddingFunction(
            api_key=os.environ.get("OPENAI_API_KEY"),
            model_name="text-embedding-3-small",
        )

    @property
    def client(self) -> chromadb.Client:
        """Get or create ChromaDB client."""
        if self._client is None:
            if self.use_persistent_client and self.persist_directory:
                # Ensure directory exists
                Path(self.persist_directory).mkdir(parents=True, exist_ok=True)

                settings = Settings(
                    persist_directory=self.persist_directory,
                    anonymized_telemetry=False,
                )
                self._client = chromadb.PersistentClient(
                    path=self.persist_directory, settings=settings
                )
            else:
                # In-memory client for testing
                self._client = chromadb.Client(
                    Settings(anonymized_telemetry=False)
                )
        return self._client

    def get_collection(
        self, name: str, metadata: Optional[Dict[str, Any]] = None
    ) -> Collection:
        """
        Get or create a ChromaDB collection.

        Args:
            name: Collection name (will be prefixed)
            metadata: Optional metadata for the collection

        Returns:
            ChromaDB Collection instance
        """
        full_name = f"{self.collection_prefix}_{name}"

        if full_name not in self._collections:
            try:
                # Try to get existing collection
                self._collections[full_name] = self.client.get_collection(
                    name=full_name, embedding_function=self._embedding_function
                )
            except ValueError:
                # Collection doesn't exist, create it
                self._collections[full_name] = self.client.create_collection(
                    name=full_name,
                    embedding_function=self._embedding_function,
                    metadata=metadata
                    or {"description": f"Collection for {name}"},
                )

        return self._collections[full_name]

    def _assert_writeable(self) -> None:
        """Ensure the client is in a writeable mode."""
        if self.mode == "read":
            raise RuntimeError("This client is read-only (mode='read')")

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

        This method matches the Pinecone upsert interface for easier migration.

        Args:
            collection_name: Name of the collection
            ids: List of unique IDs
            embeddings: Optional list of embedding vectors
            documents: Optional list of documents (for auto-embedding)
            metadatas: Optional list of metadata dictionaries
        """
        self._assert_writeable()
        collection = self.get_collection(collection_name)

        # ChromaDB upsert with duplicate-safe handling
        try:
            if embeddings is not None:
                collection.upsert(
                    ids=ids,
                    embeddings=embeddings,
                    metadatas=metadatas,
                    documents=documents,
                )
            elif documents is not None:
                # Let ChromaDB handle embedding
                collection.upsert(
                    ids=ids, documents=documents, metadatas=metadatas
                )
            else:
                raise ValueError("Either embeddings or documents must be provided")
        except ValueError as e:
            if "ids already exist" in str(e):
                # For compactor: delete and retry (overwrite behavior)
                collection.delete(ids=ids)
                if embeddings is not None:
                    collection.upsert(
                        ids=ids,
                        embeddings=embeddings,
                        metadatas=metadatas,
                        documents=documents,
                    )
                else:
                    collection.upsert(
                        ids=ids, documents=documents, metadatas=metadatas
                    )
            else:
                raise
        
        # Persist to disk if using persistent client
        if self.use_persistent_client:
            self._client.persist()

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
        collection = self.get_collection(collection_name)

        if include is None:
            include = ["metadatas", "documents", "distances"]

        if query_embeddings is not None:
            return collection.query(
                query_embeddings=query_embeddings,
                n_results=n_results,
                where=where,
                include=include,
            )
        elif query_texts is not None:
            return collection.query(
                query_texts=query_texts,
                n_results=n_results,
                where=where,
                include=include,
            )
        else:
            raise ValueError(
                "Either query_embeddings or query_texts must be provided"
            )

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
        self._assert_writeable()
        collection = self.get_collection(collection_name)

        if ids is not None:
            collection.delete(ids=ids)
        elif where is not None:
            collection.delete(where=where)
        else:
            raise ValueError("Either ids or where must be provided")
        
        # Persist to disk if using persistent client
        if self.use_persistent_client:
            self._client.persist()

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
        collection = self.get_collection(collection_name)

        if include is None:
            include = ["metadatas", "documents", "embeddings"]

        return collection.get(ids=ids, include=include)

    def count(self, collection_name: str) -> int:
        """Get the number of items in a collection."""
        collection = self.get_collection(collection_name)
        return collection.count()

    def persist_and_upload_delta(
        self,
        bucket: str,
        s3_prefix: str,
        s3_client: Optional[Any] = None,
    ) -> str:
        """
        Flush the local DB to disk, upload to S3, and return the key prefix.
        
        This method is used by producer lambdas to create delta files that
        will be processed by the compactor.
        
        Args:
            bucket: S3 bucket name
            s3_prefix: S3 prefix for delta files
            s3_client: Optional boto3 S3 client (creates one if not provided)
            
        Returns:
            S3 prefix where the delta was uploaded
            
        Raises:
            RuntimeError: If not in delta mode
        """
        if self.mode != "delta":
            raise RuntimeError("persist_and_upload_delta requires mode='delta'")
        
        if not self.persist_directory:
            raise RuntimeError("persist_directory required for delta uploads")
        
        # Import boto3 here to avoid dependency if not using S3
        try:
            import boto3
        except ImportError:
            raise ImportError(
                "boto3 is required for S3 uploads. Install with: pip install boto3"
            )
        
        if s3_client is None:
            s3_client = boto3.client("s3")
        
        # Persist ChromaDB to disk
        self._client.persist()
        
        # Create unique prefix for this delta
        prefix = f"{s3_prefix.rstrip('/')}/{uuid.uuid4().hex}/"
        
        # Upload all files in the persist directory
        persist_path = Path(self.persist_directory)
        for file_path in persist_path.rglob("*"):
            if file_path.is_file():
                relative_path = file_path.relative_to(persist_path)
                s3_key = f"{prefix}{relative_path}"
                s3_client.upload_file(str(file_path), bucket, s3_key)
        
        return prefix

    def reset(self) -> None:
        """Reset the client and clear all collections (useful for testing)."""
        if self._client is not None:
            self._client.reset()
            self._collections.clear()
            self._client = None


# Singleton instance for backward compatibility
_chroma_client_instance: Optional[ChromaDBClient] = None


def get_chroma_client(
    persist_directory: Optional[str] = None, reset: bool = False
) -> ChromaDBClient:
    """
    Get or create a singleton ChromaDB client instance.

    Args:
        persist_directory: Directory for persistence (uses env var if not provided)
        reset: Whether to reset the existing client

    Returns:
        ChromaDBClient instance
    """
    global _chroma_client_instance

    if reset or _chroma_client_instance is None:
        if persist_directory is None:
            persist_directory = os.environ.get("CHROMA_PERSIST_PATH")

        _chroma_client_instance = ChromaDBClient(
            persist_directory=persist_directory,
            mode="read",  # Default to read-only for backward compatibility
        )

    return _chroma_client_instance
