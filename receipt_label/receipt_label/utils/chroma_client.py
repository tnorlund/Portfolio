"""
ChromaDB client management module.

This module provides ChromaDB integration for vector storage,
replacing the previous Pinecone implementation.
"""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import boto3

logger = logging.getLogger(__name__)

# Type checking imports (these don't run at runtime)
if TYPE_CHECKING:
    import chromadb
    from chromadb import Collection
    from chromadb.config import Settings
    from chromadb.utils import embedding_functions
    from chromadb.errors import NotFoundError

# Runtime imports - try to import ChromaDB, but don't fail if unavailable
try:
    import chromadb
    from chromadb import Collection
    from chromadb.config import Settings
    from chromadb.utils import embedding_functions
    from chromadb.errors import NotFoundError

    CHROMADB_AVAILABLE = True
except (ImportError, StopIteration) as e:
    # ChromaDB not available or telemetry initialization failed (common in Lambda)
    print(
        f"Warning: ChromaDB import failed: {e}. ChromaDB features will be disabled."
    )
    if not TYPE_CHECKING:
        chromadb = None
        Collection = None
        Settings = None
        embedding_functions = None
        NotFoundError = Exception  # Fallback to base Exception
    CHROMADB_AVAILABLE = False


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
        if not CHROMADB_AVAILABLE:
            raise RuntimeError(
                "ChromaDB is not available. This is expected in Lambda environments "
                "that don't use ChromaDB features. Install chromadb-client with "
                "proper dependencies if ChromaDB is needed."
            )

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
    def client(self) -> Optional[chromadb.Client]:
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
        # Only add underscore separator if prefix is not empty
        if self.collection_prefix:
            full_name = f"{self.collection_prefix}_{name}"
        else:
            full_name = name

        logger.debug(
            f"Getting/creating collection: '{full_name}' (prefix='{self.collection_prefix}', name='{name}')"
        )

        if full_name not in self._collections:
            try:
                # Try to get existing collection
                self._collections[full_name] = self.client.get_collection(
                    name=full_name, embedding_function=self._embedding_function
                )
            except (NotFoundError, ValueError):
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
                raise ValueError(
                    "Either embeddings or documents must be provided"
                )
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

        # ChromaDB PersistentClient auto-persists, no manual persist needed

    def query_collection(
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

        # ChromaDB PersistentClient auto-persists, no manual persist needed

    # pylint: disable=too-many-arguments
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
            RuntimeError: If not in delta mode or no files to upload
        """
        if self.mode != "delta":
            raise RuntimeError(
                "persist_and_upload_delta requires mode='delta'"
            )

        if not self.persist_directory:
            raise RuntimeError("persist_directory required for delta uploads")

        if s3_client is None:
            s3_client = boto3.client("s3")

        # Force ChromaDB to persist data to disk
        # ChromaDB's PersistentClient should auto-persist, but we need to ensure
        # the data is written before we try to upload
        logger.info(f"Persisting ChromaDB data to {self.persist_directory}")

        # Try to explicitly persist if the method exists
        if hasattr(self._client, "persist"):
            try:
                self._client.persist()
                logger.info("Explicitly called client.persist()")
            except Exception as e:
                logger.warning(f"Could not call persist(): {e}")

        # Check if any files exist in the persist directory
        persist_path = Path(self.persist_directory)
        files_to_upload = list(persist_path.rglob("*"))
        files_to_upload = [f for f in files_to_upload if f.is_file()]

        if not files_to_upload:
            logger.error(
                f"No files found in persist directory: {self.persist_directory}"
            )
            # List directory contents for debugging
            try:
                all_items = list(persist_path.rglob("*"))
                logger.error(f"Directory contents: {all_items}")
            except Exception as e:
                logger.error(f"Could not list directory: {e}")
            raise RuntimeError(
                f"No ChromaDB files found to upload in {self.persist_directory}"
            )

        logger.info(f"Found {len(files_to_upload)} files to upload to S3")

        # Create unique prefix for this delta
        prefix = f"{s3_prefix.rstrip('/')}/{uuid.uuid4().hex}/"
        logger.info(f"Uploading delta to S3 with prefix: {prefix}")

        # Upload all files in the persist directory
        uploaded_count = 0
        for file_path in files_to_upload:
            try:
                relative_path = file_path.relative_to(persist_path)
                s3_key = f"{prefix}{relative_path}"
                logger.debug(
                    f"Uploading {file_path} to s3://{bucket}/{s3_key}"
                )
                s3_client.upload_file(str(file_path), bucket, s3_key)
                uploaded_count += 1
            except Exception as e:
                logger.error(f"Failed to upload {file_path} to S3: {e}")
                raise RuntimeError(f"S3 upload failed for {file_path}: {e}")

        logger.info(
            f"Successfully uploaded {uploaded_count} files to S3 at {prefix}"
        )
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
) -> Optional[ChromaDBClient]:
    """
    Get or create a singleton ChromaDB client instance.

    Args:
        persist_directory: Directory for persistence (uses env var if not
            provided)
        reset: Whether to reset the existing client

    Returns:
        ChromaDBClient instance or None if ChromaDB is not available
    """
    if not CHROMADB_AVAILABLE:
        return None

    global _chroma_client_instance

    if reset or _chroma_client_instance is None:
        if persist_directory is None:
            persist_directory = os.environ.get("CHROMA_PERSIST_PATH")

        _chroma_client_instance = ChromaDBClient(
            persist_directory=persist_directory,
            mode="read",  # Default to read-only for backward compatibility
        )

    return _chroma_client_instance
