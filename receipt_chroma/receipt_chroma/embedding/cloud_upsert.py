"""Direct Chroma Cloud upsert for the ingest path.

Ingest already holds the freshly computed line and word vectors in memory,
but it only writes them to an S3 delta tarball. They become queryable in
Chroma Cloud -- the query target for the MCP server and the site's cache
generators -- only once the compaction Lambda merges that delta into the
shared snapshot, which happens under a global per-collection lock.

This module writes the same vectors straight to Cloud so they are queryable
seconds after upload. Callers run it *after* the delta tarball is durable,
so the delta and its ``CompactionRun`` remain the backstop and a failure
here costs latency rather than data.

Two Chroma behaviors shape the code below:

* **Metadata merges on write.** Upserting a record without a key leaves the
  old value in place, so a key that a payload builder dropped has to be sent
  explicitly as ``None`` to be cleared. See ``TOMBSTONE_KEYS``.
* **Empty metadata is rejected.** ``upsert`` raises
  ``ValueError: Expected metadata to be a non-empty dict``, which would fail
  the whole batch, so records that sanitize down to nothing are dropped.
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import (
    Any,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Union,
)

from chromadb.errors import (
    BatchSizeExceededError,
    DuplicateIDError,
    IDAlreadyExistsError,
    InvalidArgumentError,
    InvalidUUIDError,
    QuotaError,
    RateLimitError,
    UniqueConstraintError,
)

from receipt_chroma.compaction.dual_write import (
    CloudConfig,
    _sanitize_metadatas,
)
from receipt_chroma.data.chroma_client import ChromaClient

logger = logging.getLogger(__name__)

# Chroma Cloud rejects upserts larger than 300 records. 250 matches the
# batch size the delta merge and bulk sync already use.
UPSERT_BATCH_SIZE = 250

# Chroma Cloud quotas (docs.trychroma.com/cloud/quotas-limits).
MAX_METADATA_KEYS = 32
MAX_METADATA_VALUE_BYTES = 8182
MAX_DOCUMENT_BYTES = 16384
MAX_ID_BYTES = 128

# (connect, read) seconds for a single Cloud HTTP request.
DEFAULT_REQUEST_TIMEOUT: Tuple[float, float] = (10.0, 30.0)

# Wall clock for the whole upsert, across every batch.
DEFAULT_DEADLINE_SECONDS = 60.0

# Errors that mean "this record is malformed", so retrying the batch one
# record at a time can still land the good ones. Chroma Cloud maps
# server-side validation and quota 400s to InvalidArgumentError, which is a
# ChromaError and *not* a ValueError. Rate-limit and auth errors are
# deliberately excluded: splitting one rejected batch into 250 individual
# requests would make either of those strictly worse.
_VALIDATION_ERRORS: Tuple[type, ...] = (
    ValueError,
    InvalidArgumentError,
    BatchSizeExceededError,
    DuplicateIDError,
    IDAlreadyExistsError,
    InvalidUUIDError,
    UniqueConstraintError,
)

# Throttling means "stop asking", so it aborts a fan-out already in flight
# rather than pushing the remaining 249 requests through a closing door.
_THROTTLE_ERRORS: Tuple[type, ...] = (RateLimitError, QuotaError)

# Cloud attempts are abandoned at their deadline but the thread behind them
# may still be stuck in an unbounded constructor request. In a warm Lambda
# those accumulate, so refuse to start another attempt once this many are
# still alive and let compaction carry the load until they drain.
MAX_ORPHANED_ATTEMPTS = 3
_orphaned_attempts = threading.Semaphore(MAX_ORPHANED_ATTEMPTS)

# Keys the payload builders drop rather than emit when the underlying value
# is absent (``metadata.pop(...)`` in embedding/metadata/*.py). Chroma merges
# on write, so each must be sent as ``None`` to clear a stale value left by
# an earlier ingest of the same record.
_WORD_TOMBSTONE_KEYS = (
    "label_confidence",
    "label_proposed_by",
    "label_validated_at",
    "valid_labels_array",
    "invalid_labels_array",
    "normalized_phone_10",
    "normalized_full_address",
    "normalized_url",
)
_LINE_TOMBSTONE_KEYS = (
    # Legacy neighbour fields. Row payloads no longer emit them, but row ids
    # reuse the primary line id (embedding/records.py), so a record written
    # before the row rewrite still carries them until they are cleared.
    "prev_line",
    "next_line",
    "label_status",
    "valid_labels_array",
    "invalid_labels_array",
    "section_label",
    "merchant_name",
    "anchor_phone",
    "anchor_address",
    "anchor_url",
    "normalized_phone_10",
    "normalized_full_address",
    "normalized_url",
)
TOMBSTONE_KEYS: Dict[str, Tuple[str, ...]] = {
    "words": _WORD_TOMBSTONE_KEYS,
    "lines": _LINE_TOMBSTONE_KEYS,
}

# Identity and geometry keys kept first when a record somehow exceeds the
# 32-key ceiling, so truncation is deterministic and never drops identity.
_PRIORITY_KEYS = (
    "image_id",
    "receipt_id",
    "line_id",
    "word_id",
    "text",
    "source",
    "label_status",
    "merchant_name",
)

CloudConfigLike = Union[CloudConfig, Mapping[str, str]]


# Grace allowed for one best-effort telemetry call once the budget is gone.
TAIL_GRACE_SECONDS = 0.25


def emit_within_budget(
    emit: Any,
    *args: Any,
    grace_seconds: float = TAIL_GRACE_SECONDS,
    **kwargs: Any,
) -> bool:
    """Make a telemetry call that cannot extend an overrun.

    Logging and metric emission are I/O. Once the deadline is spent, a
    blocked handler or a stalled stdout would hand the overrun straight back
    to the caller the deadline was meant to protect, so the call runs on a
    daemon thread and is abandoned after ``grace_seconds``.

    Returns True if the call finished within the grace period.
    """
    done = threading.Event()

    def _run() -> None:
        try:
            emit(*args, **kwargs)
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        finally:
            done.set()

    threading.Thread(
        target=_run, name="cloud-upsert-tail", daemon=True
    ).start()
    return done.wait(grace_seconds)


def _log_within_budget(
    log: Any, deadline_at: float, message: str, *args: Any
) -> None:
    """Log normally while there is budget, best-effort once there is not."""
    if time.monotonic() <= deadline_at:
        log.warning(message, *args)
        return
    emit_within_budget(log.warning, message, *args)


class ColumnLengthMismatch(ValueError):
    """Payload columns disagree on length, so records would be misaligned."""


@dataclass
class CloudUpsertResult:
    """Outcome of a direct Chroma Cloud upsert.

    Attributes:
        collection: Collection name the upsert targeted
        enabled: Whether a cloud write was attempted at all
        attempted: Records the payload asked to write
        upserted: Records confirmed written
        batches: Batches the payload was split into
        failed_batches: Batches that failed even after per-record fallback
        dropped: Records skipped because they could not be made valid
        truncated: Records whose metadata or document was shortened
        deadline_exceeded: Whether the wall-clock budget stopped the run
        error: First error encountered, formatted as ``Type: message``
        duration_seconds: Wall clock spent in this call
        drop_reasons: Count of dropped records keyed by reason
    """

    collection: str
    enabled: bool = True
    attempted: int = 0
    upserted: int = 0
    batches: int = 0
    failed_batches: int = 0
    dropped: int = 0
    truncated: int = 0
    deadline_exceeded: bool = False
    error: Optional[str] = None
    duration_seconds: float = 0.0
    drop_reasons: Dict[str, int] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        """Whether every record reached Cloud."""
        return (
            self.error is None
            and self.failed_batches == 0
            and self.dropped == 0
            and not self.deadline_exceeded
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to a JSON-serializable dictionary."""
        return {
            "collection": self.collection,
            "enabled": self.enabled,
            "attempted": self.attempted,
            "upserted": self.upserted,
            "batches": self.batches,
            "failed_batches": self.failed_batches,
            "dropped": self.dropped,
            "truncated": self.truncated,
            "deadline_exceeded": self.deadline_exceeded,
            "error": self.error,
            "duration_seconds": round(self.duration_seconds, 3),
            "success": self.success,
            "drop_reasons": dict(self.drop_reasons),
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


def _create_cloud_client(
    cloud_config: CloudConfig,
    collection_name: str,
    request_timeout: Optional[Tuple[float, float]],
) -> ChromaClient:
    """Create a write-mode Chroma Cloud client with bounded requests."""
    logger.debug(
        "Creating Chroma Cloud client for ingest upsert",
        extra={
            "collection": collection_name,
            "tenant": cloud_config.tenant,
            "database": cloud_config.database,
        },
    )
    return ChromaClient(
        cloud_api_key=cloud_config.api_key,
        cloud_tenant=cloud_config.tenant,
        cloud_database=cloud_config.database,
        mode="write",
        metadata_only=False,  # Vectors are supplied; matches bulk sync
        cloud_request_timeout=request_timeout,
    )


def _value_bytes(value: Any) -> int:
    """Approximate the wire size of a metadata value."""
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    if isinstance(value, list):
        return sum(_value_bytes(item) for item in value)
    return len(str(value).encode("utf-8"))


def _truncate_value(value: Any) -> Any:
    """Shrink an oversized metadata value below the Cloud limit."""
    if isinstance(value, str):
        return value.encode("utf-8")[:MAX_METADATA_VALUE_BYTES].decode(
            "utf-8", errors="ignore"
        )
    if isinstance(value, list):
        kept: List[Any] = []
        used = 0
        for item in value:
            size = _value_bytes(item)
            if used + size > MAX_METADATA_VALUE_BYTES:
                break
            kept.append(item)
            used += size
        return kept
    return value


def _limit_keys(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Keep at most ``MAX_METADATA_KEYS`` keys, identity keys first."""
    if len(metadata) <= MAX_METADATA_KEYS:
        return metadata
    ordered = [key for key in _PRIORITY_KEYS if key in metadata]
    ordered += sorted(key for key in metadata if key not in _PRIORITY_KEYS)
    return {key: metadata[key] for key in ordered[:MAX_METADATA_KEYS]}


def _prepare_metadata(
    metadata: Optional[Mapping[str, Any]],
    tombstone_keys: Sequence[str],
) -> Tuple[Optional[Dict[str, Any]], bool]:
    """Add tombstones and enforce Cloud limits for one record.

    Returns the prepared metadata (``None`` when nothing publishable is
    left) and whether anything had to be truncated.
    """
    prepared: Dict[str, Any] = dict(metadata or {})
    truncated = False

    for key, value in list(prepared.items()):
        if value is None:
            continue
        if _value_bytes(value) > MAX_METADATA_VALUE_BYTES:
            prepared[key] = _truncate_value(value)
            truncated = True

    # A record with no real values left is not worth a tombstone-only write.
    if not any(value is not None for value in prepared.values()):
        return None, truncated

    for key in tombstone_keys:
        prepared.setdefault(key, None)

    limited = _limit_keys(prepared)
    if len(limited) != len(prepared):
        truncated = True
    return limited, truncated


def _prepare_document(document: Optional[str]) -> Tuple[Optional[str], bool]:
    """Enforce the Cloud document size limit."""
    if not isinstance(document, str):
        return document, False
    encoded = document.encode("utf-8")
    if len(encoded) <= MAX_DOCUMENT_BYTES:
        return document, False
    return encoded[:MAX_DOCUMENT_BYTES].decode("utf-8", errors="ignore"), True


def _validate_column_lengths(payload: Mapping[str, Any], count: int) -> None:
    """Chroma requires every supplied column to match ``ids`` in length."""
    for column in ("embeddings", "documents", "metadatas"):
        values = payload.get(column)
        if values is not None and len(values) != count:
            raise ColumnLengthMismatch(
                f"payload column {column!r} has {len(values)} entries but "
                f"ids has {count}; refusing to upsert misaligned records"
            )


@dataclass
class _Record:
    """One prepared record, ready to send."""

    id: str
    embedding: Optional[List[float]]
    document: Optional[str]
    metadata: Dict[str, Any]


def _count_drop(result: CloudUpsertResult, reason: str) -> None:
    """Record one dropped record under ``reason``."""
    result.dropped += 1
    result.drop_reasons[reason] = result.drop_reasons.get(reason, 0) + 1


def _build_records(
    payload: Mapping[str, Any],
    ids: Sequence[str],
    tombstone_keys: Sequence[str],
    result: CloudUpsertResult,
    log: Any,
) -> List[_Record]:
    """Sanitize every record, dropping the ones Cloud would reject."""
    embeddings = payload.get("embeddings")
    documents = payload.get("documents")
    raw_metadatas = _sanitize_metadatas(
        list(payload.get("metadatas") or []) or None
    )

    records: List[_Record] = []
    for index, record_id in enumerate(ids):
        if len(str(record_id).encode("utf-8")) > MAX_ID_BYTES:
            # Truncating an ID would corrupt identity, so drop the record
            # and let compaction publish it from the delta instead.
            _count_drop(result, "id_too_long")
            log.warning(
                "Dropping record with oversized id from cloud upsert: "
                "collection=%s id_prefix=%s",
                result.collection,
                str(record_id)[:40],
            )
            continue

        metadata_in = (
            raw_metadatas[index] if raw_metadatas is not None else None
        )
        metadata, meta_truncated = _prepare_metadata(
            metadata_in, tombstone_keys
        )
        if metadata is None:
            _count_drop(result, "empty_metadata")
            log.warning(
                "Dropping record with no publishable metadata from cloud "
                "upsert (Chroma rejects empty metadata): collection=%s id=%s",
                result.collection,
                record_id,
            )
            continue

        document, doc_truncated = _prepare_document(
            documents[index] if documents is not None else None
        )
        if meta_truncated or doc_truncated:
            result.truncated += 1

        records.append(
            _Record(
                id=record_id,
                embedding=(
                    embeddings[index] if embeddings is not None else None
                ),
                document=document,
                metadata=metadata,
            )
        )
    return records


def _upsert_records(
    client: ChromaClient,
    collection_name: str,
    records: Sequence[_Record],
) -> None:
    """Send a group of prepared records in one call."""
    client.upsert(
        collection_name=collection_name,
        ids=[record.id for record in records],
        embeddings=(
            [record.embedding for record in records]
            if records and records[0].embedding is not None
            else None
        ),
        documents=(
            [record.document for record in records]
            if records and records[0].document is not None
            else None
        ),
        metadatas=[record.metadata for record in records],
    )


def _upsert_batch(
    client: ChromaClient,
    collection_name: str,
    batch: Sequence[_Record],
    result: CloudUpsertResult,
    log: Any,
    deadline_at: Optional[float] = None,
) -> int:
    """Upsert one batch, falling back to per-record on a validation error.

    A validation error means one record is malformed, so a single bad record
    must not cost the other 249. The fallback re-checks the deadline on every
    record: 250 individual requests are 250 chances to stall.
    """
    try:
        _upsert_records(client, collection_name, batch)
        return len(batch)
    except _VALIDATION_ERRORS as batch_error:
        log.warning(
            "Cloud upsert batch rejected, retrying per record: "
            "collection=%s size=%d error=%s: %s",
            collection_name,
            len(batch),
            type(batch_error).__name__,
            batch_error,
        )

    written = 0
    for index, record in enumerate(batch):
        if deadline_at is not None and time.monotonic() > deadline_at:
            remaining = len(batch) - index
            result.deadline_exceeded = True
            if result.error is None:
                result.error = (
                    f"deadline exceeded during per-record fallback with "
                    f"{remaining} record(s) unwritten"
                )
            log.warning(
                "Cloud upsert per-record fallback hit the deadline; "
                "leaving %d record(s) to compaction: collection=%s",
                remaining,
                collection_name,
            )
            break
        try:
            _upsert_records(client, collection_name, [record])
            written += 1
        except _THROTTLE_ERRORS as throttle_error:
            # Keeping the fan-out going here is what turns one rejected
            # batch into 250 throttled requests. Abandon the rest of this
            # batch to compaction instead.
            remaining = len(batch) - index
            for _ in range(remaining):
                _count_drop(result, "rate_limited_abort")
            if result.error is None:
                result.error = (
                    f"{type(throttle_error).__name__}: {throttle_error}"
                )
            log.warning(
                "Cloud upsert throttled during per-record fallback; "
                "abandoning %d record(s) to compaction: "
                "collection=%s error=%s",
                remaining,
                collection_name,
                throttle_error,
            )
            break
        except Exception as e:  # pylint: disable=broad-exception-caught
            _count_drop(result, "rejected")
            if result.error is None:
                result.error = f"{type(e).__name__}: {e}"
            log.warning(
                "Cloud upsert rejected record: collection=%s id=%s error=%s",
                collection_name,
                record.id,
                e,
            )
    return written


def upsert_payload_to_cloud(
    payload: Mapping[str, Any],
    collection_name: str,
    cloud_config: Optional[CloudConfigLike],
    *,
    batch_size: int = UPSERT_BATCH_SIZE,
    deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
    request_timeout: Optional[Tuple[float, float]] = DEFAULT_REQUEST_TIMEOUT,
    log: Optional[Any] = None,
) -> CloudUpsertResult:
    """Upsert a Chroma payload directly to Chroma Cloud.

    This never raises. Callers treat the cloud write as best effort: the
    delta tarball and ``CompactionRun`` uploaded before it are the durable
    path, so a cloud failure costs latency, not data.

    Args:
        payload: ``{"ids", "embeddings", "documents", "metadatas"}`` as built
            by ``build_row_payload`` / ``build_word_payload``
        collection_name: Target collection ("lines" or "words")
        cloud_config: ``CloudConfig``, or a dict with ``api_key``/``tenant``/
            ``database``. ``None`` (or incomplete) means cloud is disabled and
            the call is a no-op.
        batch_size: Records per upsert, capped at ``UPSERT_BATCH_SIZE``
        deadline_seconds: Hard wall-clock budget covering connection setup and
            every write. Work not finished by then is abandoned to the
            compaction backstop rather than eating the caller's runtime.
        request_timeout: ``(connect, read)`` seconds per HTTP request
        log: Optional logger; defaults to this module's logger

    Returns:
        CloudUpsertResult describing what reached Cloud
    """
    start = time.monotonic()
    active_log = log or logger
    config = _coerce_cloud_config(cloud_config)

    if config is None:
        return CloudUpsertResult(
            collection=collection_name,
            enabled=False,
            duration_seconds=time.monotonic() - start,
        )

    ids: List[str] = list(payload.get("ids") or [])
    result = CloudUpsertResult(
        collection=collection_name,
        attempted=len(ids),
    )
    if not ids:
        result.duration_seconds = time.monotonic() - start
        return result

    # Record preparation is local and cannot stall, so keep it outside the
    # deadline thread where its errors report normally.
    try:
        _validate_column_lengths(payload, len(ids))
        records = _build_records(
            payload,
            ids,
            TOMBSTONE_KEYS.get(collection_name, ()),
            result,
            active_log,
        )
    except Exception as e:  # pylint: disable=broad-exception-caught
        result.error = f"{type(e).__name__}: {e}"
        active_log.warning(
            "Chroma Cloud upsert failed before any write (non-fatal): "
            "collection=%s error=%s",
            collection_name,
            result.error,
        )
        result.duration_seconds = time.monotonic() - start
        return result

    if not records:
        result.duration_seconds = time.monotonic() - start
        return result

    effective_batch = max(1, min(batch_size, UPSERT_BATCH_SIZE))
    deadline_at = start + deadline_seconds

    # Everything below talks to Cloud, including client construction: Chroma
    # issues identity, tenant and database requests inside CloudClient()
    # before our session timeout can be applied, so a stall there would be
    # invisible to a per-request timeout. Run it on a daemon thread and join
    # with the remaining budget; a thread we abandon dies with the process
    # and cannot hold the Lambda open.
    def _connect_and_write() -> None:
        client = None
        try:
            client = _create_cloud_client(
                config, collection_name, request_timeout
            )
            for start_idx in range(0, len(records), effective_batch):
                if time.monotonic() > deadline_at:
                    remaining = len(records) - start_idx
                    result.deadline_exceeded = True
                    if result.error is None:
                        result.error = (
                            f"deadline of {deadline_seconds:.0f}s exceeded "
                            f"with {remaining} record(s) unwritten"
                        )
                    active_log.warning(
                        "Cloud upsert hit its wall-clock budget; leaving %d "
                        "record(s) to compaction: collection=%s",
                        remaining,
                        collection_name,
                    )
                    break

                batch = records[start_idx : start_idx + effective_batch]
                result.batches += 1
                try:
                    result.upserted += _upsert_batch(
                        client,
                        collection_name,
                        batch,
                        result,
                        active_log,
                        deadline_at,
                    )
                except Exception as e:  # pylint: disable=broad-except
                    result.failed_batches += 1
                    if result.error is None:
                        result.error = f"{type(e).__name__}: {e}"
                    active_log.warning(
                        "Chroma Cloud upsert batch failed "
                        "(non-fatal, compaction is the backstop): "
                        "collection=%s size=%d error=%s",
                        collection_name,
                        len(batch),
                        result.error,
                    )
        except Exception as e:  # pylint: disable=broad-exception-caught
            if result.error is None:
                result.error = f"{type(e).__name__}: {e}"
            active_log.warning(
                "Chroma Cloud upsert failed (non-fatal): "
                "collection=%s error=%s",
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
            _orphaned_attempts.release()

    # Backpressure: if enough previous attempts are still stuck, this one
    # would only add another thread and another session to a warm container.
    if not _orphaned_attempts.acquire(blocking=False):
        result.error = (
            f"more than {MAX_ORPHANED_ATTEMPTS} cloud upsert attempts are "
            "still in flight; skipping this one"
        )
        _count_drop(result, "orphaned_threads")
        active_log.warning(
            "Skipping Chroma Cloud upsert: %d earlier attempt(s) have not "
            "finished; leaving this receipt to compaction: collection=%s",
            MAX_ORPHANED_ATTEMPTS,
            collection_name,
        )
        result.duration_seconds = time.monotonic() - start
        return result

    worker = threading.Thread(
        target=_connect_and_write,
        name=f"cloud-upsert-{collection_name}",
        daemon=True,
    )
    worker.start()
    worker.join(max(0.0, deadline_at - time.monotonic()))

    if worker.is_alive():
        result.deadline_exceeded = True
        if result.error is None:
            result.error = (
                f"deadline of {deadline_seconds:.0f}s exceeded while "
                "connecting or writing to Chroma Cloud"
            )
        # The budget is spent, so this is the one line we still pay for --
        # a blocked log sink here would put the overrun back on the caller.
        _log_within_budget(
            active_log,
            deadline_at,
            "Cloud upsert abandoned at its wall-clock budget; compaction "
            "remains the backstop: collection=%s",
            collection_name,
        )

    result.duration_seconds = time.monotonic() - start
    return result
