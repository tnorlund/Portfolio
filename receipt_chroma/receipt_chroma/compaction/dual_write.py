"""Dual-write support for ChromaDB compaction.

This module provides functionality to write compaction updates to both
a local ChromaDB snapshot (for S3) and Chroma Cloud simultaneously.

Architecture:
    DynamoDB (source of truth)
        │
        ▼ DynamoDB Streams
    Compaction Process
        │
        ├─────────────────────────────────────┐
        ▼                                     ▼
    S3 Snapshot                         Chroma Cloud
    (primary)                           (replica, non-blocking)

Design decisions:
    - Local writes are primary; cloud failures don't block local success
    - Cloud errors are logged but don't fail the batch
    - Same process_collection_updates() function is reused for both targets
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from receipt_chroma.compaction.models import CollectionUpdateResult
from receipt_chroma.compaction.processor import process_collection_updates
from receipt_chroma.data.chroma_client import ChromaClient
from receipt_dynamo.constants import ChromaDBCollection
from receipt_dynamo.data.dynamo_client import DynamoClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CloudConfig:
    """Configuration for Chroma Cloud connection.

    Attributes:
        api_key: Chroma Cloud API key
        tenant: Chroma Cloud tenant ID (defaults to "default")
        database: Chroma Cloud database name (defaults to "default")
        enabled: Whether dual-write is enabled
    """

    api_key: str
    tenant: Optional[str] = None
    database: Optional[str] = None
    enabled: bool = True

    @classmethod
    def from_env(
        cls, env: Optional[Dict[str, str]] = None
    ) -> Optional["CloudConfig"]:
        """Create CloudConfig from environment variables.

        Args:
            env: Optional dict of environment variables (uses os.environ if None)

        Returns:
            CloudConfig if enabled and API key is present, None otherwise
        """
        import os

        env = env or dict(os.environ)

        enabled = env.get("CHROMA_CLOUD_ENABLED", "false").lower() == "true"
        if not enabled:
            return None

        api_key = env.get("CHROMA_CLOUD_API_KEY", "").strip()
        if not api_key:
            logger.warning(
                "CHROMA_CLOUD_ENABLED=true but CHROMA_CLOUD_API_KEY not set"
            )
            return None

        return cls(
            api_key=api_key,
            tenant=env.get("CHROMA_CLOUD_TENANT") or None,
            database=env.get("CHROMA_CLOUD_DATABASE") or None,
            enabled=True,
        )


@dataclass
class DualWriteResult:
    """Result from dual-write operation.

    Attributes:
        local_result: Result from local ChromaDB write (always present)
        cloud_result: Result from Chroma Cloud write (None if disabled/failed)
        cloud_error: Error message if cloud write failed
        cloud_enabled: Whether cloud write was attempted
    """

    local_result: CollectionUpdateResult
    cloud_result: Optional[CollectionUpdateResult] = None
    cloud_error: Optional[str] = None
    cloud_enabled: bool = False

    @property
    def has_errors(self) -> bool:
        """Whether any writes had errors (local or cloud)."""
        if self.local_result.has_errors:
            return True
        if self.cloud_result and self.cloud_result.has_errors:
            return True
        return False

    @property
    def cloud_success(self) -> bool:
        """Whether cloud write succeeded (False if disabled or failed)."""
        return (
            self.cloud_enabled
            and self.cloud_result is not None
            and self.cloud_error is None
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format."""
        result = {
            "local_result": self.local_result.to_dict(),
            "cloud_enabled": self.cloud_enabled,
            "cloud_success": self.cloud_success,
        }
        if self.cloud_result:
            result["cloud_result"] = self.cloud_result.to_dict()
        if self.cloud_error:
            result["cloud_error"] = self.cloud_error
        return result


def _create_cloud_client(
    cloud_config: CloudConfig,
    collection: ChromaDBCollection,
) -> ChromaClient:
    """Create a ChromaClient configured for Chroma Cloud.

    Args:
        cloud_config: Cloud configuration with API key, tenant, database
        collection: Collection for logging context

    Returns:
        ChromaClient configured for Chroma Cloud
    """
    logger.debug(
        "Creating Chroma Cloud client",
        extra={
            "collection": collection.value,
            "tenant": cloud_config.tenant or "default",
            "database": cloud_config.database or "default",
        },
    )

    return ChromaClient(
        cloud_api_key=cloud_config.api_key,
        cloud_tenant=cloud_config.tenant,
        cloud_database=cloud_config.database,
        mode="write",
        metadata_only=True,
    )


def apply_collection_updates(
    stream_messages: List[Any],
    collection: ChromaDBCollection,
    local_client: ChromaClient,
    cloud_config: Optional[CloudConfig],
    op_logger: Any,
    metrics: Optional[Any] = None,
    dynamo_client: Optional[DynamoClient] = None,
) -> DualWriteResult:
    """Apply collection updates to local ChromaDB and optionally to Chroma Cloud.

    This function orchestrates dual-write by:
    1. Applying updates to the local ChromaDB client (for S3 snapshot)
    2. If cloud_config is provided, applying the same updates to Chroma Cloud

    Cloud writes are non-blocking - failures are logged but don't fail the batch.
    The local write is always the primary target.

    Args:
        stream_messages: List of StreamMessage objects to process
        collection: Target collection (LINES or WORDS)
        local_client: ChromaClient for local snapshot (already open)
        cloud_config: Optional CloudConfig for Chroma Cloud dual-write
        op_logger: Logger instance for observability
        metrics: Optional metrics collector
        dynamo_client: Optional DynamoDB client for operations

    Returns:
        DualWriteResult with results from both local and cloud writes
    """
    # Phase 1: Apply updates to LOCAL client (primary)
    op_logger.info(
        "Applying updates to local snapshot",
        collection=collection.value,
        message_count=len(stream_messages),
    )

    local_result = process_collection_updates(
        stream_messages=stream_messages,
        collection=collection,
        chroma_client=local_client,
        logger=op_logger,
        metrics=metrics,
        dynamo_client=dynamo_client,
    )

    op_logger.info(
        "Local updates applied",
        collection=collection.value,
        metadata_updates=local_result.total_metadata_updated,
        label_updates=local_result.total_labels_updated,
        delta_merges=local_result.delta_merge_count,
        has_errors=local_result.has_errors,
    )

    # Phase 2: Apply updates to CHROMA CLOUD (if enabled)
    cloud_result = None
    cloud_error = None
    cloud_enabled = cloud_config is not None and cloud_config.enabled

    if cloud_enabled:
        try:
            cloud_client = _create_cloud_client(cloud_config, collection)
            with cloud_client:
                op_logger.info(
                    "Applying updates to Chroma Cloud",
                    collection=collection.value,
                    message_count=len(stream_messages),
                )

                cloud_result = process_collection_updates(
                    stream_messages=stream_messages,
                    collection=collection,
                    chroma_client=cloud_client,
                    logger=op_logger,
                    metrics=metrics,
                    dynamo_client=dynamo_client,
                )

            op_logger.info(
                "Cloud updates applied",
                collection=collection.value,
                metadata_updates=cloud_result.total_metadata_updated,
                label_updates=cloud_result.total_labels_updated,
            )

        except Exception as e:  # pylint: disable=broad-exception-caught
            # Cloud errors are logged but don't fail the batch
            # S3 upload proceeds independently
            cloud_error = f"{type(e).__name__}: {e}"
            op_logger.error(
                "Cloud updates failed (non-blocking)",
                error=cloud_error,
                collection=collection.value,
                exc_info=True,
            )

    # Track metrics
    if metrics:
        # Track cloud-specific metrics
        if cloud_result:
            metrics.gauge(
                "CompactionCloudMetadataUpdated",
                cloud_result.total_metadata_updated,
                {"collection": collection.value},
            )
            metrics.gauge(
                "CompactionCloudLabelsUpdated",
                cloud_result.total_labels_updated,
                {"collection": collection.value},
            )

        if cloud_error:
            metrics.count(
                "CompactionCloudError",
                1,
                {"collection": collection.value},
            )

        # Track dual-write status for observability
        metrics.count(
            "CompactionDualWriteStatus",
            1,
            {
                "collection": collection.value,
                "local_success": (
                    "true" if not local_result.has_errors else "false"
                ),
                "cloud_success": (
                    "true" if cloud_result and not cloud_error else "false"
                ),
                "cloud_enabled": "true" if cloud_enabled else "false",
            },
        )

    return DualWriteResult(
        local_result=local_result,
        cloud_result=cloud_result,
        cloud_error=cloud_error,
        cloud_enabled=cloud_enabled,
    )


@dataclass
class BulkSyncResult:
    """Result from bulk sync operation.

    Attributes:
        total_items: Total items in local collection
        uploaded: Number of items successfully uploaded
        failed_batches: Number of batches that failed after retries
        cloud_count: Final count in cloud collection
        error: Error message if sync failed completely
        duration_seconds: Total sync duration
    """

    total_items: int
    uploaded: int
    failed_batches: int
    cloud_count: int
    error: Optional[str] = None
    duration_seconds: float = 0.0

    @property
    def success(self) -> bool:
        """Whether sync completed without errors."""
        return self.error is None and self.failed_batches == 0


def _upload_batch_with_retry(
    cloud_coll: Any,
    batch: Dict[str, Any],
    max_retries: int,
) -> int:
    """Upload a single batch with exponential backoff retry.

    Args:
        cloud_coll: Cloud collection to upload to
        batch: Batch data with ids, embeddings, metadatas, documents
        max_retries: Maximum retry attempts

    Returns:
        Number of items uploaded

    Raises:
        Exception: If all retries fail
    """
    last_error = None

    for attempt in range(max_retries):
        try:
            cloud_coll.upsert(
                ids=batch["ids"],
                embeddings=batch.get("embeddings"),
                metadatas=batch.get("metadatas"),
                documents=batch.get("documents"),
            )
            return len(batch["ids"])
        except Exception as e:  # pylint: disable=broad-exception-caught
            last_error = e
            if attempt < max_retries - 1:
                wait_time = 1.0 * (2**attempt)  # 1s, 2s, 4s
                time.sleep(wait_time)

    raise last_error  # type: ignore[misc]


def _create_cloud_client_for_sync(
    cloud_config: CloudConfig,
    collection_name: str,
) -> ChromaClient:
    """Create a ChromaClient configured for Chroma Cloud sync.

    Args:
        cloud_config: Cloud configuration with API key, tenant, database
        collection_name: Collection name for logging context

    Returns:
        ChromaClient configured for Chroma Cloud
    """
    logger.debug(
        "Creating Chroma Cloud client for sync",
        extra={
            "collection": collection_name,
            "tenant": cloud_config.tenant or "default",
            "database": cloud_config.database or "default",
        },
    )

    return ChromaClient(
        cloud_api_key=cloud_config.api_key,
        cloud_tenant=cloud_config.tenant,
        cloud_database=cloud_config.database,
        mode="write",
        metadata_only=False,  # Need full data for sync
    )


def sync_collection_to_cloud(
    local_client: ChromaClient,
    collection_name: str,
    cloud_config: CloudConfig,
    batch_size: int = 5000,
    max_workers: int = 4,
    max_retries: int = 3,
    logger: Optional[Any] = None,
) -> BulkSyncResult:
    """Sync a local ChromaDB collection to Chroma Cloud.

    Uses parallel batch uploads for efficiency. Cloud failures are
    non-blocking - errors are logged but don't raise exceptions.

    Args:
        local_client: ChromaClient with local snapshot (already open)
        collection_name: Name of collection to sync (e.g., "lines", "words")
        cloud_config: CloudConfig with API key, tenant, database
        batch_size: Embeddings per batch (default 5000)
        max_workers: Parallel upload threads (default 4)
        max_retries: Retries per batch with exponential backoff
        logger: Optional logger instance

    Returns:
        BulkSyncResult with sync statistics
    """
    start_time = time.time()
    log = logger or logging.getLogger(__name__)

    try:
        # Get local collection
        local_coll = local_client.get_collection(collection_name)
        total_count = local_coll.count()

        if total_count == 0:
            return BulkSyncResult(
                total_items=0,
                uploaded=0,
                failed_batches=0,
                cloud_count=0,
                duration_seconds=time.time() - start_time,
            )

        # Create cloud client
        cloud_client = _create_cloud_client_for_sync(
            cloud_config, collection_name
        )

        with cloud_client:
            cloud_coll = cloud_client.get_collection(
                collection_name,
                create_if_missing=True,
            )

            # Prepare batches (paginated reads)
            batches: List[Dict[str, Any]] = []
            for offset in range(0, total_count, batch_size):
                batch_data = local_coll.get(
                    include=["embeddings", "metadatas", "documents"],
                    limit=batch_size,
                    offset=offset,
                )
                if batch_data.get("ids"):
                    batches.append(batch_data)

            log.info(
                "Starting parallel cloud sync",
                collection=collection_name,
                total_items=total_count,
                batch_count=len(batches),
                batch_size=batch_size,
                max_workers=max_workers,
            )

            # Parallel upload with ThreadPoolExecutor
            uploaded = 0
            failed_batches = 0

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(
                        _upload_batch_with_retry,
                        cloud_coll,
                        batch,
                        max_retries,
                    ): i
                    for i, batch in enumerate(batches)
                }

                for future in as_completed(futures):
                    batch_idx = futures[future]
                    try:
                        count = future.result()
                        uploaded += count
                    except (
                        Exception
                    ) as e:  # pylint: disable=broad-exception-caught
                        failed_batches += 1
                        log.error(
                            f"Batch {batch_idx} failed after {max_retries} retries",
                            error=str(e),
                            batch_idx=batch_idx,
                        )

            cloud_count = cloud_coll.count()

        return BulkSyncResult(
            total_items=total_count,
            uploaded=uploaded,
            failed_batches=failed_batches,
            cloud_count=cloud_count,
            duration_seconds=time.time() - start_time,
        )

    except Exception as e:  # pylint: disable=broad-exception-caught
        return BulkSyncResult(
            total_items=0,
            uploaded=0,
            failed_batches=0,
            cloud_count=0,
            error=f"{type(e).__name__}: {e}",
            duration_seconds=time.time() - start_time,
        )
