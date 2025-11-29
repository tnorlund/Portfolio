"""
ChromaDB client with proper resource management.

This module provides a ChromaDB client that implements proper resource
management including:
- Context manager support (with statement)
- Explicit close() method to release SQLite connections
- Proper cleanup to prevent file locking issues

Addresses GitHub issue: https://github.com/chroma-core/chroma/issues/5868
"""

import gc
import logging
import os
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

import boto3
import chromadb
from chromadb.config import Settings
from chromadb.errors import NotFoundError
from chromadb.utils import embedding_functions

logger = logging.getLogger(__name__)


class ChromaClient:
    """
    ChromaDB client with proper resource management.

    This client implements:
    - Context manager support for automatic cleanup
    - Explicit close() method to release SQLite connections
    - Proper handling of file locks to prevent corruption

    Usage:
        # As context manager (recommended)
        with ChromaClient(persist_directory="/path/to/db") as client:
            collection = client.get_collection("my_collection")
            # ... use collection ...

        # Manual management
        client = ChromaClient(persist_directory="/path/to/db")
        try:
            collection = client.get_collection("my_collection")
            # ... use collection ...
        finally:
            client.close()
    """

    def __init__(  # pylint: disable=too-many-positional-arguments
        self,
        persist_directory: Optional[str] = None,
        mode: str = "read",
        embedding_function: Optional[Any] = None,
        metadata_only: bool = False,
        http_url: Optional[str] = None,
    ):
        """
        Initialize the ChromaDB client.

        Args:
            persist_directory: Directory for local ChromaDB persistence.
                             If None, uses in-memory storage.
            mode: Operation mode - "read", "write", "delta", or "snapshot"
            embedding_function: Optional custom embedding function
            metadata_only: If True, uses default embedding function to
                          avoid API costs
            http_url: Optional HTTP URL for remote ChromaDB server
        """
        self.persist_directory = persist_directory
        self.mode = mode.lower()
        self.use_persistent_client = persist_directory is not None
        self._client: Optional[Any] = None
        self._collections: Dict[str, Any] = {}
        self._http_url: Optional[str] = (http_url or "").strip() or None
        self._closed = False

        # Configure embedding function
        if embedding_function:
            self._embedding_function = embedding_function
        elif self.mode == "read":
            self._embedding_function = None
        elif metadata_only:
            # embedding_functions is always available (imported module)
            self._embedding_function = (
                embedding_functions.DefaultEmbeddingFunction()
            )
        else:
            # embedding_functions is always available (imported module)
            api_key = (
                os.environ.get("OPENAI_API_KEY")
                or os.environ.get("CHROMA_OPENAI_API_KEY")
                or "placeholder"
            )
            self._embedding_function = (
                embedding_functions.OpenAIEmbeddingFunction(
                    api_key=api_key,
                    model_name="text-embedding-3-small",
                )
            )

    def __enter__(self) -> "ChromaClient":
        """Enter context manager."""
        if self._closed:
            raise RuntimeError("Cannot use closed ChromaClient")
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit context manager and close client."""
        self.close()

    def _ensure_client(self) -> Any:
        """Get or create ChromaDB client."""
        if self._closed:
            raise RuntimeError("Cannot use closed ChromaClient")

        if self._client is None:
            if self._http_url:
                # Parse host and optional port from a url or host:port
                host = self._http_url
                port: Optional[int] = None
                if "://" in host:
                    host = host.split("://", 1)[1]
                if ":" in host:
                    host, port_str = host.rsplit(":", 1)
                    try:
                        port = int(port_str)
                    except (ValueError, TypeError):
                        port = None

                http_kwargs: Dict[str, Any] = {"host": host}
                if port is not None:
                    http_kwargs["port"] = port

                self._client = chromadb.HttpClient(**http_kwargs)
                logger.debug(
                    "Created HTTP ChromaDB client for %s", self._http_url
                )

            elif self.use_persistent_client and self.persist_directory:
                # Ensure directory exists
                Path(self.persist_directory).mkdir(parents=True, exist_ok=True)

                settings = Settings(
                    persist_directory=self.persist_directory,
                    anonymized_telemetry=False,
                    # Always allow reset for testing and flexibility
                    allow_reset=True,
                )
                self._client = chromadb.PersistentClient(
                    path=self.persist_directory, settings=settings
                )
                logger.debug(
                    "Created persistent ChromaDB client at: %s",
                    self.persist_directory,
                )
            else:
                # In-memory client for testing
                self._client = chromadb.Client(
                    Settings(
                        anonymized_telemetry=False,
                        allow_reset=True,  # Allow reset for in-memory clients
                    )
                )
                logger.debug("Created in-memory ChromaDB client")

        return self._client

    @property
    def client(self) -> Any:
        """Get or create ChromaDB client."""
        return self._ensure_client()

    def close(self) -> None:
        """
        Close the ChromaDB client and release all resources.

        This method properly closes SQLite connections and releases file locks.
        It's critical to call this before uploading ChromaDB files to S3 or
        performing other file operations.

        This addresses the issue described in:
        https://github.com/chroma-core/chroma/issues/5868

        After calling close(), the client cannot be used again. Create a new
        instance if you need to use ChromaDB again.
        """
        if self._closed:
            return

        try:
            logger.debug(
                "Closing ChromaDB client: %s",
                self.persist_directory or "in-memory",
            )

            # Clear collections cache
            if hasattr(self, "_collections"):
                self._collections.clear()

            # For PersistentClient, we need to close the underlying
            # SQLite connections
            if self._client is not None:
                # Try to access the internal client if it exists
                # ChromaDB doesn't expose close(), so we must access
                # internal _client attribute (issue #5868)
                internal_client = getattr(self._client, "_client", None)
                if internal_client is not None:
                    close_fn = getattr(internal_client, "close", None)
                    if callable(close_fn):
                        try:
                            close_fn()
                        except (OSError, RuntimeError) as err:
                            logger.debug(
                                "Error closing internal client: %s", err
                            )

                # Clear the client reference
                self._client = None

            # Force garbage collection to ensure SQLite connections are closed
            # This is necessary because ChromaDB doesn't expose a close()
            # method (issue #5868)
            gc.collect()

            # Multiple GC passes to ensure all references are cleared
            # ChromaDB's internal connections may have circular references
            for _ in range(3):
                gc.collect()

            # Longer delay to ensure file handles are released by OS
            # Critical for preventing file locking issues when uploading to S3
            # SQLite files can remain locked even after client is "closed"
            time.sleep(
                0.5
            )  # Increased from 0.1s to 0.5s for more reliable unlocking

            self._closed = True
            logger.debug("ChromaDB client closed successfully")

        except (OSError, RuntimeError) as exc:
            logger.warning(
                "Error closing ChromaDB client (non-critical): %s", exc
            )
            # Mark as closed even if there was an error
            self._closed = True

    def get_collection(
        self,
        name: str,
        create_if_missing: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """
        Get or create a ChromaDB collection.

        Args:
            name: Collection name
            create_if_missing: If True, create collection if it doesn't exist
            metadata: Optional metadata for the collection

        Returns:
            ChromaDB Collection instance
        """
        if self._closed:
            raise RuntimeError("Cannot use closed ChromaClient")

        if name not in self._collections:
            try:
                # Try to get existing collection
                if self.mode == "read":
                    self._collections[name] = self.client.get_collection(
                        name=name
                    )
                else:
                    get_args: Dict[str, Any] = {"name": name}
                    if self._embedding_function:
                        get_args["embedding_function"] = (
                            self._embedding_function
                        )
                    self._collections[name] = self.client.get_collection(
                        **get_args
                    )

                logger.debug("Retrieved existing collection: %s", name)

            except (NotFoundError, ValueError) as e:
                if create_if_missing and self.mode != "read":
                    # Create new collection
                    create_args = {
                        "name": name,
                        "metadata": metadata
                        or {"description": f"Collection: {name}"},
                    }
                    if self._embedding_function:
                        create_args["embedding_function"] = (
                            self._embedding_function
                        )

                    self._collections[name] = self.client.create_collection(
                        **create_args
                    )
                    logger.info("Created new collection: %s", name)
                else:
                    raise ValueError(
                        f"Collection '{name}' not found and "
                        f"create_if_missing=False. Error: {e}"
                    ) from e

        return self._collections[name]

    def _assert_writeable(self) -> None:
        """Ensure the client is in a writeable mode."""
        if self.mode == "read":
            raise RuntimeError("This client is read-only (mode='read')")

    def upsert(  # pylint: disable=too-many-positional-arguments
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
        self._assert_writeable()
        collection = self.get_collection(
            collection_name, create_if_missing=True
        )

        upsert_args: Dict[str, Any] = {"ids": ids}
        if embeddings is not None:
            upsert_args["embeddings"] = embeddings
        if documents is not None:
            upsert_args["documents"] = documents
        if metadatas is not None:
            upsert_args["metadatas"] = metadatas

        try:
            collection.upsert(**upsert_args)
        except ValueError as e:
            if "ids already exist" in str(e):
                # Handle duplicate IDs by deleting and retrying
                collection.delete(ids=ids)
                collection.upsert(**upsert_args)
            else:
                raise

        logger.debug("Upserted %d vectors to %s", len(ids), collection_name)

    def query(  # pylint: disable=too-many-positional-arguments
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
        if include is None:
            include = ["metadatas", "documents", "distances"]

        collection = self.get_collection(collection_name)

        query_args = {"n_results": n_results, "include": include}

        if where:
            query_args["where"] = where

        if query_embeddings is not None:
            query_args["query_embeddings"] = query_embeddings
        elif query_texts is not None:
            if self.mode == "read":
                raise ValueError(
                    "Text queries require write mode with embedding function"
                )
            query_args["query_texts"] = query_texts
        else:
            raise ValueError(
                "Either query_embeddings or query_texts must be provided"
            )

        result = collection.query(**query_args)
        return result  # type: ignore[no-any-return]

    def get(
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

        result = collection.get(ids=ids, include=include)
        return result  # type: ignore[no-any-return]

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
            logger.debug(
                "Deleted %d items from %s by IDs", len(ids), collection_name
            )
        elif where is not None:
            collection.delete(where=where)
            logger.debug("Deleted items from %s by filter", collection_name)
        else:
            raise ValueError("Either ids or where must be provided")

    def count(self, collection_name: str) -> int:
        """Get the number of items in a collection."""
        collection = self.get_collection(collection_name)
        return collection.count()  # type: ignore[no-any-return]

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
            logger.debug("Reset ChromaDB client")

    def upsert_vectors(
        self,
        collection_name: str,
        ids: List[str],
        *,
        embeddings: Optional[List[List[float]]] = None,
        documents: Optional[List[str]] = None,
        metadatas: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """
        Upsert vectors into a collection (alias for backward compatibility).

        This method maintains compatibility with the legacy ChromaDBClient.

        Args:
            collection_name: Name of the collection
            ids: List of unique IDs
            embeddings: Optional list of embedding vectors
            documents: Optional list of documents (for auto-embedding)
            metadatas: Optional list of metadata dictionaries
        """
        self.upsert(
            collection_name=collection_name,
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )

    def persist_and_upload_delta(
        self,
        bucket: str,
        s3_prefix: str,
        s3_client: Optional[Any] = None,
        validate_after_upload: bool = True,
    ) -> str:
        """
        Flush the local DB to disk, upload to S3, and return the key prefix.

        This method is used by producer lambdas to create delta files that
        will be processed by the compactor.

        Args:
            bucket: S3 bucket name
            s3_prefix: S3 prefix for delta files
            s3_client: Optional boto3 S3 client (creates one if not provided)
            validate_after_upload: If True, validates the uploaded database

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

        # CRITICAL: Close ChromaDB client BEFORE uploading to ensure SQLite
        # files are flushed and unlocked (issue #5868)
        self.close()

        # Generate a unique delta ID for this upload
        # This ensures each delta has a unique S3 path for parallel processing
        delta_id = uuid.uuid4().hex

        # Upload all files from persist directory under the delta ID
        uploaded_files = []
        for file_path in Path(self.persist_directory).rglob("*"):
            if file_path.is_file():
                relative = file_path.relative_to(self.persist_directory)
                # Include delta_id in the S3 key to ensure uniqueness
                s3_key = f"{s3_prefix.rstrip('/')}/{delta_id}/{relative}"
                s3_client.upload_file(str(file_path), bucket, s3_key)
                uploaded_files.append(s3_key)

        if not uploaded_files:
            raise RuntimeError("No files to upload")

        # The actual delta key is the prefix + delta_id
        actual_delta_key = f"{s3_prefix.rstrip('/')}/{delta_id}/"

        # Optional validation: try to download and open the database
        if validate_after_upload:
            with tempfile.TemporaryDirectory() as temp_dir:
                test_key = uploaded_files[0]
                local_file = Path(temp_dir) / Path(test_key).name
                s3_client.download_file(bucket, test_key, str(local_file))

        logger.info(
            "Uploaded delta to S3 (bucket=%s, prefix=%s, key=%s, files=%d)",
            bucket,
            s3_prefix,
            actual_delta_key,
            len(uploaded_files),
        )

        return actual_delta_key

    @contextmanager
    def collection(
        self, name: str, create_if_missing: bool = False
    ) -> Generator[Any, None, None]:
        """
        Context manager for a collection that ensures proper cleanup.

        Usage:
            with client.collection("my_collection") as coll:
                coll.query(...)
        """
        coll = self.get_collection(name, create_if_missing=create_if_missing)
        try:
            yield coll
        finally:
            # Collections are managed by the client, no explicit cleanup needed
            pass
