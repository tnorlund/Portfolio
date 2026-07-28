"""Direct Chroma Cloud upsert for the ingest path.

Ingest already holds the freshly computed line and word vectors in memory,
but it only writes them to an S3 delta tarball. They become queryable in
Chroma Cloud -- the query target for the MCP server and the site's cache
generators -- only once the compaction Lambda merges that delta into the
shared snapshot, which happens under a global per-collection lock.

This module writes the same vectors straight to Cloud so they are queryable
seconds after upload. The delta tarball and ``CompactionRun`` are unchanged,
so compaction remains the backstop and this write can fail without
regressing today's behavior.
"""

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union

from receipt_chroma.compaction.dual_write import (
    CloudConfig,
    _create_cloud_client_for_sync,
    _sanitize_metadatas,
)

logger = logging.getLogger(__name__)

# Chroma Cloud rejects upserts larger than 300 records. 250 matches the
# batch size the delta merge and bulk sync already use.
UPSERT_BATCH_SIZE = 250

CloudConfigLike = Union[CloudConfig, Mapping[str, str]]


@dataclass
class CloudUpsertResult:
    """Outcome of a direct Chroma Cloud upsert.

    Attributes:
        collection: Collection name the upsert targeted
        enabled: Whether a cloud write was attempted at all
        attempted: Records the payload asked to write
        upserted: Records confirmed written
        batches: Batches the payload was split into
        failed_batches: Batches that raised after the client's own retries
        error: First error encountered, formatted as ``Type: message``
        duration_seconds: Wall clock spent in this call
    """

    collection: str
    enabled: bool = True
    attempted: int = 0
    upserted: int = 0
    batches: int = 0
    failed_batches: int = 0
    error: Optional[str] = None
    duration_seconds: float = 0.0

    @property
    def success(self) -> bool:
        """Whether every record reached Cloud."""
        return self.error is None and self.failed_batches == 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to a JSON-serializable dictionary."""
        return {
            "collection": self.collection,
            "enabled": self.enabled,
            "attempted": self.attempted,
            "upserted": self.upserted,
            "batches": self.batches,
            "failed_batches": self.failed_batches,
            "error": self.error,
            "duration_seconds": round(self.duration_seconds, 3),
            "success": self.success,
        }


def _coerce_cloud_config(
    cloud_config: Optional[CloudConfigLike],
) -> Optional[CloudConfig]:
    """Accept either a ``CloudConfig`` or the ingest path's dict form."""
    if cloud_config is None:
        return None
    if isinstance(cloud_config, CloudConfig):
        return cloud_config if cloud_config.enabled else None

    api_key = (cloud_config.get("api_key") or "").strip()
    tenant = (cloud_config.get("tenant") or "").strip()
    database = (cloud_config.get("database") or "").strip()
    if not (api_key and tenant and database):
        return None
    return CloudConfig(
        api_key=api_key,
        tenant=tenant,
        database=database,
        enabled=True,
    )


def _slice_optional(
    values: Optional[Sequence[Any]], start: int, end: int
) -> Optional[List[Any]]:
    """Slice a payload column, tolerating a missing column."""
    if values is None:
        return None
    return list(values[start:end])


def _clean_metadatas(
    metadatas: Optional[Sequence[Any]],
) -> Optional[List[Dict[str, Any]]]:
    """Drop keys Chroma Cloud rejects and normalize empties to ``{}``.

    ``_sanitize_metadatas`` emits ``None`` for a record whose keys were all
    oversized; ``ChromaClient.upsert`` cannot normalize ``None``.
    """
    if metadatas is None:
        return None
    sanitized = _sanitize_metadatas(list(metadatas))
    return [md or {} for md in (sanitized or [])]


def upsert_payload_to_cloud(
    payload: Mapping[str, Any],
    collection_name: str,
    cloud_config: Optional[CloudConfigLike],
    *,
    batch_size: int = UPSERT_BATCH_SIZE,
    log: Optional[Any] = None,
) -> CloudUpsertResult:
    """Upsert a Chroma payload directly to Chroma Cloud.

    This never raises. Callers treat the cloud write as best effort: the
    delta tarball and ``CompactionRun`` written alongside it are the
    durable path, so a cloud failure costs latency, not data.

    Args:
        payload: ``{"ids", "embeddings", "documents", "metadatas"}`` as built
            by ``build_row_payload`` / ``build_word_payload``
        collection_name: Target collection ("lines" or "words")
        cloud_config: ``CloudConfig``, or a dict with ``api_key``/``tenant``/
            ``database``. ``None`` (or incomplete) means cloud is disabled and
            the call is a no-op.
        batch_size: Records per upsert, capped at ``UPSERT_BATCH_SIZE``
        log: Optional logger; defaults to this module's logger

    Returns:
        CloudUpsertResult describing what reached Cloud
    """
    start = time.time()
    active_log = log or logger
    config = _coerce_cloud_config(cloud_config)

    if config is None:
        return CloudUpsertResult(
            collection=collection_name,
            enabled=False,
            duration_seconds=time.time() - start,
        )

    ids: List[str] = list(payload.get("ids") or [])
    result = CloudUpsertResult(
        collection=collection_name,
        attempted=len(ids),
    )
    if not ids:
        result.duration_seconds = time.time() - start
        return result

    effective_batch = max(1, min(batch_size, UPSERT_BATCH_SIZE))
    embeddings = payload.get("embeddings")
    documents = payload.get("documents")
    metadatas = payload.get("metadatas")

    client = None
    try:
        client = _create_cloud_client_for_sync(config, collection_name)
        for start_idx in range(0, len(ids), effective_batch):
            end_idx = min(start_idx + effective_batch, len(ids))
            result.batches += 1
            try:
                client.upsert(
                    collection_name=collection_name,
                    ids=ids[start_idx:end_idx],
                    embeddings=_slice_optional(embeddings, start_idx, end_idx),
                    documents=_slice_optional(documents, start_idx, end_idx),
                    metadatas=_clean_metadatas(
                        _slice_optional(metadatas, start_idx, end_idx)
                    ),
                )
                result.upserted += end_idx - start_idx
            except Exception as e:  # pylint: disable=broad-exception-caught
                result.failed_batches += 1
                if result.error is None:
                    result.error = f"{type(e).__name__}: {e}"
                active_log.warning(
                    "Chroma Cloud upsert batch failed "
                    "(non-fatal, compaction is the backstop): "
                    "collection=%s batch=%d-%d error=%s",
                    collection_name,
                    start_idx,
                    end_idx,
                    result.error,
                )
    except Exception as e:  # pylint: disable=broad-exception-caught
        result.error = f"{type(e).__name__}: {e}"
        active_log.warning(
            "Chroma Cloud upsert failed before any batch was written "
            "(non-fatal): collection=%s error=%s",
            collection_name,
            result.error,
        )
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:  # pylint: disable=broad-exception-caught
                active_log.debug(
                    "Chroma Cloud client close failed for %s",
                    collection_name,
                    exc_info=True,
                )

    result.duration_seconds = time.time() - start
    return result
