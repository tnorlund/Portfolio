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
from dataclasses import dataclass
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
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> Optional["CloudConfig"]:
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
                "local_success": "true" if not local_result.has_errors else "false",
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
