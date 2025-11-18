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
import shutil
import tempfile
import time
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
    logger.warning(
        "ChromaDB import failed: %s. ChromaDB features will be disabled.", e
    )
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
        http_url: Optional[str] = None,
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
        self._client: Optional[Any] = None
        self._collections: Dict[str, Any] = {}
        self._http_url: Optional[str] = (http_url or "").strip() or None

        # Configure embedding function based on mode and settings
        if embedding_function:
            self._embedding_function = embedding_function
        elif self.mode == "read":
            # Read mode doesn't need embedding function
            self._embedding_function = None
        elif metadata_only:
            # For metadata-only operations, use default embedding function
            if embedding_functions:
                self._embedding_function = (
                    embedding_functions.DefaultEmbeddingFunction()
                )
            else:
                self._embedding_function = None
        else:
            # Use OpenAI for write operations by default
            if embedding_functions:
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
            else:
                self._embedding_function = None

    @property
    def client(self) -> Any:
        """Get or create ChromaDB client."""
        if self._client is None:
            # Prefer remote HTTP client when http_url is provided
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
                    except Exception:
                        port = None

                http_kwargs: Dict[str, Any] = {"host": host}
                if port is not None:
                    http_kwargs["port"] = port

                self._client = chromadb.HttpClient(**http_kwargs)  # type: ignore
                logger.debug(
                    "Created HTTP ChromaDB client for %s", self._http_url
                )

            elif self.use_persistent_client and self.persist_directory:
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
                logger.debug(
                    "Created persistent ChromaDB client at: %s",
                    self.persist_directory,
                )
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
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Get or create a ChromaDB collection."""
        logger.debug("Getting/creating collection: '%s'", name)

        if name not in self._collections:
            try:
                # Try to get existing collection
                if self.mode == "read":
                    # For read mode, don't specify embedding function
                    self._collections[name] = self.client.get_collection(
                        name=name
                    )
                else:
                    # For write modes, include embedding function if available
                    get_args = {"name": name}
                    if self._embedding_function:
                        get_args["embedding_function"] = (
                            self._embedding_function
                        )
                    self._collections[name] = self.client.get_collection(
                        **get_args
                    )

                logger.debug(
                    "Successfully retrieved existing collection: %s", name
                )

            except (NotFoundError, ValueError, Exception) as e:
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
                    logger.info(
                        "Successfully created new collection: %s", name
                    )
                else:
                    raise ValueError(
                        f"Collection '{name}' not found and create_if_missing=False. Error: {e}"
                    )

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
        collection = self.get_collection(
            collection_name, create_if_missing=True
        )

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
        # Ensure client is initialized; if HTTP URL is configured but the SDK
        # is unavailable, gracefully return an empty result to mirror legacy behavior.
        _ = self.client
        if self._client is None and self._http_url:
            return {"metadatas": [[]], "documents": [[]], "distances": [[]]}

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
                raise ValueError(
                    "Text queries require write mode with embedding function"
                )
            query_args["query_texts"] = query_texts
        else:
            raise ValueError(
                "Either query_embeddings or query_texts must be provided"
            )

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

    def _close_client_for_upload(self) -> None:
        """
        Close ChromaDB client to ensure SQLite files are flushed and unlocked.

        This is a workaround because ChromaDB's PersistentClient doesn't expose a close() method.
        We clear references, force garbage collection, and add a small delay to ensure
        SQLite connections are closed before uploading files to S3.

        See: docs/CHROMADB_CLIENT_CLOSING_WORKAROUND.md
        """
        if self._client is None:
            return

        try:
            logger.info(
                "Closing ChromaDB client before upload: %s",
                self.persist_directory,
            )

            # Clear collections cache
            if hasattr(self, "_collections"):
                self._collections.clear()

            # Clear the underlying client reference
            if hasattr(self._client, "_client") and self._client._client is not None:
                self._client._client = None

            # Force garbage collection to ensure SQLite connections are closed
            import gc

            gc.collect()

            # Small delay to ensure file handles are released by OS
            import time as _time

            _time.sleep(0.1)

            logger.info("ChromaDB client closed successfully")
        except Exception as e:
            logger.warning(
                "Error closing ChromaDB client (non-critical): %s", e
            )

    def _validate_delta_after_upload(
        self, bucket: str, s3_prefix: str, s3_client: Optional[Any] = None
    ) -> tuple[bool, float]:
        """
        Validate that a delta uploaded to S3 can be opened and read by ChromaDB.

        Downloads the delta from S3 to a temporary directory and attempts to open it
        with ChromaDB to verify it's not corrupted.

        Args:
            bucket: S3 bucket name
            s3_prefix: S3 prefix where delta was uploaded
            s3_client: Optional boto3 S3 client (creates one if not provided)

        Returns:
            Tuple of (success: bool, duration_seconds: float)
        """
        validation_start_time = time.time()

        if s3_client is None:
            s3_client = boto3.client("s3")

        temp_dir = None
        try:
            temp_dir = tempfile.mkdtemp()
            logger.info(
                "Validating delta by downloading from S3: %s (temp_dir: %s)",
                s3_prefix,
                temp_dir,
            )

            # Download delta from S3
            paginator = s3_client.get_paginator("list_objects_v2")
            pages = paginator.paginate(Bucket=bucket, Prefix=s3_prefix)

            downloaded_files = 0
            for page in pages:
                if "Contents" not in page:
                    continue

                for obj in page["Contents"]:
                    key = obj["Key"]
                    relative_path = key[len(s3_prefix) :]
                    if not relative_path:
                        continue

                    local_file = os.path.join(temp_dir, relative_path)
                    os.makedirs(os.path.dirname(local_file), exist_ok=True)
                    s3_client.download_file(bucket, key, local_file)
                    downloaded_files += 1

            if downloaded_files == 0:
                validation_duration = time.time() - validation_start_time
                logger.error("No files found in S3 prefix: %s (duration: %.2fs)", s3_prefix, validation_duration)
                return False, validation_duration

            logger.info("Downloaded %d files for validation", downloaded_files)

            # Check for SQLite files
            temp_path = Path(temp_dir)
            sqlite_files = list(temp_path.rglob("*.sqlite*"))
            if not sqlite_files:
                validation_duration = time.time() - validation_start_time
                logger.error("No SQLite files found in downloaded delta (duration: %.2fs)", validation_duration)
                return False, validation_duration

            # Try to open with ChromaDB
            logger.info("Attempting to open delta with ChromaDB for validation")
            try:
                # Create a new client to test the downloaded delta
                test_client = chromadb.PersistentClient(path=temp_dir)
                collections = test_client.list_collections()

                if not collections:
                    validation_duration = time.time() - validation_start_time
                    logger.error("No collections found in delta (duration: %.2fs)", validation_duration)
                    return False, validation_duration

                # Try to read from each collection
                for collection_meta in collections:
                    collection = test_client.get_collection(collection_meta.name)
                    count = collection.count()
                    logger.info(
                        "Collection '%s' validated: %d embeddings",
                        collection_meta.name,
                        count,
                    )

                # Clean up test client
                del test_client
                import gc

                gc.collect()

                validation_duration = time.time() - validation_start_time
                logger.info(
                    "Delta validation successful: %s (collections: %d, duration: %.2fs)",
                    s3_prefix,
                    len(collections),
                    validation_duration,
                )
                return True, validation_duration

            except Exception as e:
                validation_duration = time.time() - validation_start_time
                logger.error(
                    "Failed to open delta with ChromaDB during validation %s: %s (type: %s, duration: %.2fs)",
                    s3_prefix,
                    e,
                    type(e).__name__,
                    validation_duration,
                    exc_info=True,
                )
                return False, validation_duration

        except Exception as e:
            validation_duration = time.time() - validation_start_time
            logger.error(
                "Error during delta validation %s: %s (type: %s, duration: %.2fs)",
                s3_prefix,
                e,
                type(e).__name__,
                validation_duration,
                exc_info=True,
            )
            return False, validation_duration
        finally:
            # Clean up temp directory
            if temp_dir:
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                except Exception:
                    pass

    # Additional ChromaDB-specific methods for backward compatibility

    def persist_and_upload_delta(
        self,
        bucket: str,
        s3_prefix: str,
        s3_client: Optional[Any] = None,
        max_retries: int = 3,
        validate_after_upload: bool = True,
    ) -> str:
        """
        Flush the local DB to disk, upload to S3, validate, and return the key prefix.

        This method is used by producer lambdas to create delta files that
        will be processed by the compactor. It includes validation to ensure
        the delta can be opened after upload, with retry logic if validation fails.

        Args:
            bucket: S3 bucket name
            s3_prefix: S3 prefix for delta files
            s3_client: Optional boto3 S3 client (creates one if not provided)
            max_retries: Maximum number of retry attempts if validation fails (default: 3)
            validate_after_upload: Whether to validate the delta after upload (default: True)

        Returns:
            S3 prefix where the delta was uploaded

        Raises:
            RuntimeError: If upload or validation fails after all retries
        """
        if self.mode not in ("delta", "write"):
            raise RuntimeError(
                "persist_and_upload_delta requires mode='delta' or mode='write'"
            )

        if not self.persist_directory:
            raise RuntimeError("persist_directory required for delta uploads")

        if s3_client is None:
            s3_client = boto3.client("s3")

        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    logger.info(
                        "Retry attempt %d/%d for delta upload",
                        attempt + 1,
                        max_retries,
                    )
                    # Small delay before retry
                    time.sleep(0.5 * attempt)

                # Force ChromaDB to persist data to disk
                logger.info("Persisting ChromaDB data to %s", self.persist_directory)

                # Try to explicitly persist if the method exists
                if hasattr(self._client, "persist"):
                    try:
                        self._client.persist()
                        logger.info("Explicitly called client.persist()")
                    except Exception as e:
                        logger.warning("Could not call persist(): %s", e)

                # CRITICAL: Close ChromaDB client BEFORE uploading to ensure SQLite files are flushed and unlocked
                # This prevents corruption when uploading files that are still being written to
                # See: docs/CHROMADB_CLIENT_CLOSING_WORKAROUND.md
                self._close_client_for_upload()

                # Check if any files exist in the persist directory
                persist_path = Path(self.persist_directory)
                files_to_upload = list(persist_path.rglob("*"))
                files_to_upload = [f for f in files_to_upload if f.is_file()]

                if not files_to_upload:
                    logger.error(
                        "No files found in persist directory: %s",
                        self.persist_directory,
                    )
                    raise RuntimeError(
                        f"No ChromaDB files found to upload in {self.persist_directory}"
                    )

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
                        logger.debug(
                            "Uploading %s to s3://%s/%s", file_path, bucket, s3_key
                        )
                        s3_client.upload_file(str(file_path), bucket, s3_key)
                        uploaded_count += 1
                    except Exception as e:
                        logger.error("Failed to upload %s to S3: %s", file_path, e)
                        raise RuntimeError(f"S3 upload failed for {file_path}: {e}")

                logger.info(
                    "Successfully uploaded %d files to S3 at %s",
                    uploaded_count,
                    prefix,
                )

                # Validate the uploaded delta if requested
                if validate_after_upload:
                    logger.info(
                        "Starting delta validation (attempt %d/%d) for prefix: %s",
                        attempt + 1,
                        max_retries,
                        prefix,
                    )
                    validation_result, validation_duration = self._validate_delta_after_upload(
                        bucket, prefix, s3_client
                    )
                    if validation_result:
                        logger.info(
                            "Delta validation successful: %s (attempt %d/%d, duration: %.2fs)",
                            prefix,
                            attempt + 1,
                            max_retries,
                            validation_duration,
                        )
                        return prefix
                    else:
                        logger.warning(
                            "Delta validation failed for %s (attempt %d/%d, duration: %.2fs)",
                            prefix,
                            attempt + 1,
                            max_retries,
                            validation_duration,
                        )
                        # Delete the failed upload to avoid leaving corrupted deltas
                        try:
                            logger.info("Cleaning up failed delta upload: %s", prefix)
                            paginator = s3_client.get_paginator("list_objects_v2")
                            pages = paginator.paginate(Bucket=bucket, Prefix=prefix)
                            objects_to_delete = []
                            for page in pages:
                                if "Contents" in page:
                                    for obj in page["Contents"]:
                                        objects_to_delete.append({"Key": obj["Key"]})
                            if objects_to_delete:
                                s3_client.delete_objects(
                                    Bucket=bucket,
                                    Delete={"Objects": objects_to_delete},
                                )
                                logger.info(
                                    "Deleted %d objects from failed upload",
                                    len(objects_to_delete),
                                )
                        except Exception as cleanup_error:
                            logger.warning(
                                "Failed to cleanup failed upload: %s", cleanup_error
                            )

                        # If this was the last attempt, raise an error
                        if attempt == max_retries - 1:
                            raise RuntimeError(
                                f"Delta validation failed after {max_retries} attempts. "
                                f"Last prefix: {prefix}"
                            )
                        # Otherwise, continue to retry
                        continue
                else:
                    # No validation requested, return immediately
                    logger.info(
                        "Skipping validation (validate_after_upload=False) for prefix: %s",
                        prefix,
                    )
                    return prefix

            except RuntimeError as e:
                # Re-raise RuntimeError (includes validation failures on last attempt)
                logger.error(
                    "RuntimeError during delta upload/validation (attempt %d/%d): %s",
                    attempt + 1,
                    max_retries,
                    e,
                    exc_info=True,
                )
                raise
            except Exception as e:
                logger.error(
                    "Unexpected error during delta upload (attempt %d/%d): %s (type: %s)",
                    attempt + 1,
                    max_retries,
                    e,
                    type(e).__name__,
                    exc_info=True,
                )
                if attempt == max_retries - 1:
                    raise RuntimeError(
                        f"Delta upload failed after {max_retries} attempts: {e}"
                    ) from e
                # Continue to retry for other exceptions

        # Should never reach here, but just in case
        raise RuntimeError(f"Delta upload failed after {max_retries} attempts")
