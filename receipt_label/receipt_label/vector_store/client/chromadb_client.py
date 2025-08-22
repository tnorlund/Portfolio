"""
ChromaDB client implementation for vector storage.

This module consolidates the functionality from the old scattered ChromaDB files:
- chroma_client.py (main client)
- chroma_client_refactored.py (alternative implementation)
- Parts of chroma_compactor.py (client operations)

The implementation provides a clean, unified ChromaDB client that implements
the VectorStoreInterface for consistency and extensibility.
"""

import logging
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import boto3

from .base import VectorStoreInterface

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
    logger.warning("ChromaDB import failed: %s. ChromaDB features will be disabled.", e)
    if not TYPE_CHECKING:
        chromadb = None
        Collection = None
        Settings = None
        embedding_functions = None
        NotFoundError = Exception  # Fallback to base Exception
    CHROMADB_AVAILABLE = False


class ChromaDBClient(VectorStoreInterface):
    """
    ChromaDB implementation of the vector store interface.
    
    This class consolidates functionality from multiple old ChromaDB files
    and provides a clean, consistent interface for vector operations.
    
    Supports multiple operation modes:
    - "read": Read-only operations (no embedding function needed)
    - "write": Full read-write operations with embedding support
    - "delta": Write mode for creating delta files
    - "snapshot": Full snapshot operations
    """

    def __init__(
        self,
        persist_directory: Optional[str] = None,
        mode: str = "read",
        embedding_function: Optional[Any] = None,
        metadata_only: bool = False,
    ):
        """
        Initialize the ChromaDB client.

        Args:
            persist_directory: Directory for local ChromaDB persistence.
                             If None, uses in-memory storage.
            mode: Operation mode - "read", "write", "delta", or "snapshot"
            embedding_function: Optional custom embedding function
            metadata_only: If True, uses default embedding function to avoid API costs
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

        # Configure embedding function based on mode and settings
        if embedding_function:
            self._embedding_function = embedding_function
        elif self.mode == "read":
            # Read mode doesn't need embedding function
            self._embedding_function = None
        elif metadata_only:
            # For metadata-only operations, use default embedding function
            self._embedding_function = embedding_functions.DefaultEmbeddingFunction()
        else:
            # Use OpenAI for write operations by default
            api_key = (
                os.environ.get("OPENAI_API_KEY") 
                or os.environ.get("CHROMA_OPENAI_API_KEY") 
                or "placeholder"
            )
            self._embedding_function = embedding_functions.OpenAIEmbeddingFunction(
                api_key=api_key,
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
                    allow_reset=self.mode in ("write", "delta", "snapshot"),
                )
                self._client = chromadb.PersistentClient(
                    path=self.persist_directory, settings=settings
                )
                logger.debug("Created persistent ChromaDB client at: %s", self.persist_directory)
            else:
                # In-memory client for testing
                self._client = chromadb.Client(
                    Settings(anonymized_telemetry=False)
                )
                logger.debug("Created in-memory ChromaDB client")
        return self._client

    def get_collection(
        self, 
        name: str, 
        create_if_missing: bool = False,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Collection:
        """Get or create a ChromaDB collection."""
        logger.debug("Getting/creating collection: '%s'", name)

        if name not in self._collections:
            try:
                # Try to get existing collection
                if self.mode == "read":
                    # For read mode, don't specify embedding function
                    self._collections[name] = self.client.get_collection(name=name)
                else:
                    # For write modes, include embedding function if available
                    get_args = {"name": name}
                    if self._embedding_function:
                        get_args["embedding_function"] = self._embedding_function
                    self._collections[name] = self.client.get_collection(**get_args)
                
                logger.debug("Successfully retrieved existing collection: %s", name)
                
            except (NotFoundError, ValueError, Exception) as e:
                if create_if_missing and self.mode != "read":
                    # Create new collection
                    create_args = {
                        "name": name,
                        "metadata": metadata or {"description": f"Collection: {name}"},
                    }
                    if self._embedding_function:
                        create_args["embedding_function"] = self._embedding_function
                        
                    self._collections[name] = self.client.create_collection(**create_args)
                    logger.info("Successfully created new collection: %s", name)
                else:
                    raise ValueError(f"Collection '{name}' not found and create_if_missing=False. Error: {e}")

        return self._collections[name]

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
        """Upsert vectors into a collection."""
        self._assert_writeable()
        collection = self.get_collection(collection_name, create_if_missing=True)

        # ChromaDB upsert with duplicate-safe handling
        try:
            upsert_args = {"ids": ids}
            if embeddings is not None:
                upsert_args["embeddings"] = embeddings
            if documents is not None:
                upsert_args["documents"] = documents
            if metadatas is not None:
                upsert_args["metadatas"] = metadatas

            collection.upsert(**upsert_args)
            
        except ValueError as e:
            if "ids already exist" in str(e):
                # Handle duplicate IDs by deleting and retrying
                collection.delete(ids=ids)
                collection.upsert(**upsert_args)
            else:
                raise

        logger.debug("Upserted %d vectors to %s", len(ids), collection_name)

    def query(
        self,
        collection_name: str,
        query_embeddings: Optional[List[List[float]]] = None,
        query_texts: Optional[List[str]] = None,
        n_results: int = 10,
        where: Optional[Dict[str, Any]] = None,
        include: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Query vectors from a collection."""
        collection = self.get_collection(collection_name)

        if include is None:
            include = ["metadatas", "documents", "distances"]

        query_args = {"n_results": n_results, "include": include}
        
        if where:
            query_args["where"] = where
            
        if query_embeddings is not None:
            query_args["query_embeddings"] = query_embeddings
        elif query_texts is not None:
            if self.mode == "read":
                raise ValueError("Text queries require write mode with embedding function")
            query_args["query_texts"] = query_texts
        else:
            raise ValueError("Either query_embeddings or query_texts must be provided")

        return collection.query(**query_args)

    def get_by_ids(
        self,
        collection_name: str,
        ids: List[str],
        include: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Get vectors by their IDs."""
        collection = self.get_collection(collection_name)

        if include is None:
            include = ["metadatas", "documents", "embeddings"]

        return collection.get(ids=ids, include=include)

    def delete(
        self,
        collection_name: str,
        ids: Optional[List[str]] = None,
        where: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Delete vectors from a collection."""
        self._assert_writeable()
        collection = self.get_collection(collection_name)

        if ids is not None:
            collection.delete(ids=ids)
            logger.debug("Deleted %d items from %s by IDs", len(ids), collection_name)
        elif where is not None:
            collection.delete(where=where)
            logger.debug("Deleted items from %s by filter", collection_name)
        else:
            raise ValueError("Either ids or where must be provided")

    def count(self, collection_name: str) -> int:
        """Get the number of items in a collection."""
        collection = self.get_collection(collection_name)
        return collection.count()

    def list_collections(self) -> List[str]:
        """List all available collections."""
        collections = self.client.list_collections()
        return [c.name for c in collections]

    def collection_exists(self, name: str) -> bool:
        """Check if a collection exists."""
        return name in self.list_collections()

    def reset(self) -> None:
        """Reset the client and clear all collections (useful for testing)."""
        if self._client is not None:
            self._client.reset()
            self._collections.clear()
            self._client = None
            logger.debug("Reset ChromaDB client")

    # Additional ChromaDB-specific methods for backward compatibility
    
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
        """
        if self.mode not in ("delta", "write"):
            raise RuntimeError(
                "persist_and_upload_delta requires mode='delta' or mode='write'"
            )

        if not self.persist_directory:
            raise RuntimeError("persist_directory required for delta uploads")

        if s3_client is None:
            s3_client = boto3.client("s3")

        # Force ChromaDB to persist data to disk
        logger.info("Persisting ChromaDB data to %s", self.persist_directory)
        
        # Try to explicitly persist if the method exists
        if hasattr(self._client, 'persist'):
            try:
                self._client.persist()
                logger.info("Explicitly called client.persist()")
            except Exception as e:
                logger.warning("Could not call persist(): %s", e)

        # Check if any files exist in the persist directory
        persist_path = Path(self.persist_directory)
        files_to_upload = list(persist_path.rglob("*"))
        files_to_upload = [f for f in files_to_upload if f.is_file()]

        if not files_to_upload:
            logger.error("No files found in persist directory: %s", self.persist_directory)
            raise RuntimeError(f"No ChromaDB files found to upload in {self.persist_directory}")

        logger.info("Found %d files to upload to S3", len(files_to_upload))

        # Create unique prefix for this delta
        prefix = f"{s3_prefix.rstrip('/')}/{uuid.uuid4().hex}/"
        logger.info("Uploading delta to S3 with prefix: %s", prefix)

        # Upload all files in the persist directory
        uploaded_count = 0
        for file_path in files_to_upload:
            try:
                relative_path = file_path.relative_to(persist_path)
                s3_key = f"{prefix}{relative_path}"
                logger.debug("Uploading %s to s3://%s/%s", file_path, bucket, s3_key)
                s3_client.upload_file(str(file_path), bucket, s3_key)
                uploaded_count += 1
            except Exception as e:
                logger.error("Failed to upload %s to S3: %s", file_path, e)
                raise RuntimeError(f"S3 upload failed for {file_path}: {e}")

        logger.info("Successfully uploaded %d files to S3 at %s", uploaded_count, prefix)
        return prefix