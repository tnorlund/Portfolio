"""
High-level embedding orchestration for receipt processing.

Provides EmbeddingResult class and create_embeddings_and_compaction_run
function that encapsulates the complete embedding workflow:
1. Download S3 snapshots -> local directories
2. Generate embeddings via OpenAI
3. Upsert to local clients (snapshot + delta merged for immediate querying)
4. Upload deltas to S3 (triggers async compaction)
5. Create CompactionRun in DynamoDB
6. Return EmbeddingResult with local ChromaClients ready for queries
"""

import json
import logging
import os
import shutil
import tempfile
import time
import uuid
from concurrent.futures import as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import boto3
from openai import OpenAI

from receipt_chroma.data.chroma_client import ChromaClient
from receipt_chroma.embedding.formatting.line_format import (
    format_line_context_embedding_input,
)
from receipt_chroma.embedding.formatting.word_format import (
    format_word_context_embedding_input,
)
from receipt_chroma.embedding.openai import embed_texts
from receipt_chroma.embedding.records import (
    LineEmbeddingRecord,
    WordEmbeddingRecord,
    build_line_payload,
    build_word_payload,
)
from receipt_chroma.s3.helpers import upload_delta_tarball
from receipt_chroma.s3.snapshot import download_snapshot_atomic
from receipt_dynamo.constants import CompactionState
from receipt_dynamo.entities import (
    CompactionRun,
    ReceiptLine,
    ReceiptPlace,
    ReceiptWord,
    ReceiptWordLabel,
)

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

    from receipt_dynamo import DynamoClient

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "text-embedding-3-small"


def _get_traceable():
    """Get the traceable decorator if langsmith is available."""
    try:
        from langsmith.run_helpers import traceable  # pylint: disable=import-outside-toplevel

        return traceable
    except ImportError:
        # Return a no-op decorator if langsmith not installed
        def noop_decorator(*args, **kwargs):
            def wrapper(fn):
                return fn

            return wrapper

        return noop_decorator


def _get_context_thread_pool_executor():
    """Get ContextThreadPoolExecutor if langsmith is available.

    ContextThreadPoolExecutor automatically propagates context variables
    (including Langsmith trace context) to child threads, enabling proper
    trace nesting. Falls back to ThreadPoolExecutor if langsmith not installed.
    """
    try:
        from langsmith.utils import ContextThreadPoolExecutor  # pylint: disable=import-outside-toplevel

        return ContextThreadPoolExecutor
    except ImportError:
        from concurrent.futures import ThreadPoolExecutor  # pylint: disable=import-outside-toplevel

        return ThreadPoolExecutor


# ============================================================================
# Langsmith-Traced Helper Functions for Parallel Execution
# These use @traceable decorator and rely on ContextThreadPoolExecutor
# to automatically propagate trace context to child threads.
# ============================================================================


def _download_lines_snapshot(
    chromadb_bucket: str,
    s3_client: "S3Client",
) -> dict[str, Any]:
    """Download lines ChromaDB snapshot from S3 (traced)."""
    traceable = _get_traceable()

    @traceable(
        name="s3_download_lines_snapshot",
        project_name="receipt-label-validation",
    )
    def _traced_download(bucket: str, client: "S3Client") -> dict[str, Any]:
        local_path = tempfile.mkdtemp(prefix="lines_snapshot_")
        result = download_snapshot_atomic(
            bucket=bucket,
            collection="lines",
            local_path=local_path,
            verify_integrity=False,
            s3_client=client,
        )
        return {
            "local_path": local_path,
            "status": result.get("status"),
            "version_id": result.get("version_id"),
        }

    return _traced_download(chromadb_bucket, s3_client)


def _download_words_snapshot(
    chromadb_bucket: str,
    s3_client: "S3Client",
) -> dict[str, Any]:
    """Download words ChromaDB snapshot from S3 (traced)."""
    traceable = _get_traceable()

    @traceable(
        name="s3_download_words_snapshot",
        project_name="receipt-label-validation",
    )
    def _traced_download(bucket: str, client: "S3Client") -> dict[str, Any]:
        local_path = tempfile.mkdtemp(prefix="words_snapshot_")
        result = download_snapshot_atomic(
            bucket=bucket,
            collection="words",
            local_path=local_path,
            verify_integrity=False,
            s3_client=client,
        )
        return {
            "local_path": local_path,
            "status": result.get("status"),
            "version_id": result.get("version_id"),
        }

    return _traced_download(chromadb_bucket, s3_client)


def _embed_lines(
    openai_client: OpenAI,
    receipt_lines: list[ReceiptLine],
    model: str,
) -> list[list[float]]:
    """Generate embeddings for lines via OpenAI (traced)."""
    traceable = _get_traceable()

    @traceable(
        name="openai_embed_lines",
        project_name="receipt-label-validation",
        metadata={"line_count": len(receipt_lines), "model": model},
    )
    def _traced_embed(
        client: OpenAI, lines: list[ReceiptLine], embedding_model: str
    ) -> list[list[float]]:
        formatted_texts = [
            format_line_context_embedding_input(ln, lines) for ln in lines
        ]
        return embed_texts(client=client, texts=formatted_texts, model=embedding_model)

    return _traced_embed(openai_client, receipt_lines, model)


def _embed_words(
    openai_client: OpenAI,
    receipt_words: list[ReceiptWord],
    model: str,
) -> list[list[float]]:
    """Generate embeddings for words via OpenAI (traced)."""
    traceable = _get_traceable()

    @traceable(
        name="openai_embed_words",
        project_name="receipt-label-validation",
        metadata={"word_count": len(receipt_words), "model": model},
    )
    def _traced_embed(
        client: OpenAI, words: list[ReceiptWord], embedding_model: str
    ) -> list[list[float]]:
        formatted_texts = [
            format_word_context_embedding_input(w, words, context_size=2)
            for w in words
        ]
        return embed_texts(client=client, texts=formatted_texts, model=embedding_model)

    return _traced_embed(openai_client, receipt_words, model)


def _download_and_embed_parallel(
    receipt_lines: list[ReceiptLine],
    receipt_words: list[ReceiptWord],
    chromadb_bucket: str,
    s3_client: "S3Client",
    openai_client: OpenAI,
    model: str,
) -> tuple[str, str, list[list[float]], list[list[float]]]:
    """
    Run all 4 I/O operations in parallel.

    Uses ContextThreadPoolExecutor from langsmith.utils to automatically
    propagate trace context to child threads, enabling proper trace nesting.

    Returns:
        Tuple of (lines_dir, words_dir, line_embeddings, word_embeddings)
    """
    thread_pool_class = _get_context_thread_pool_executor()

    with thread_pool_class(max_workers=4) as executor:
        futures = {
            executor.submit(
                _download_lines_snapshot,
                chromadb_bucket,
                s3_client,
            ): "download_lines",
            executor.submit(
                _download_words_snapshot,
                chromadb_bucket,
                s3_client,
            ): "download_words",
            executor.submit(
                _embed_lines,
                openai_client,
                receipt_lines,
                model,
            ): "embed_lines",
            executor.submit(
                _embed_words,
                openai_client,
                receipt_words,
                model,
            ): "embed_words",
        }

        results: dict[str, Any] = {}
        for future in as_completed(futures):
            task_name = futures[future]
            try:
                results[task_name] = future.result()
                logger.info("Parallel task completed: %s", task_name)
            except Exception as e:
                logger.error("Parallel task failed: %s - %s", task_name, e)
                raise

    return (
        results["download_lines"]["local_path"],
        results["download_words"]["local_path"],
        results["embed_lines"],
        results["embed_words"],
    )


@dataclass
class EmbeddingResult:
    """
    Result from embedding orchestration.

    Contains local ChromaClients with snapshot + delta merged for immediate
    querying, and the CompactionRun entity for tracking async compaction.

    Usage:
        result = create_embeddings_and_compaction_run(...)

        # Query immediately from local clients
        lines_results = result.lines_client.query(
            collection_name="lines",
            query_embeddings=[embedding],
            n_results=5
        )

        # Use cached embeddings for similarity search (no additional API calls)
        embedding = result.line_embeddings.get(line_id)

        # Optionally wait for remote compaction
        result.wait_for_compaction_to_finish(dynamo_client, max_wait_seconds=60)

        # Always close when done to release file locks
        result.close()

    Or use as context manager:
        with create_embeddings_and_compaction_run(...) as result:
            # Query from result.lines_client / result.words_client
            pass
        # Automatically closed
    """

    lines_client: ChromaClient
    words_client: ChromaClient
    compaction_run: CompactionRun

    # Private fields for temp directory cleanup (no defaults, must come first)
    _lines_dir: str = field(repr=False, default="")
    _words_dir: str = field(repr=False, default="")

    # Embedding cache for reuse in merchant resolution and label validation
    # Avoids redundant OpenAI API calls
    line_embeddings: dict[int, list[float]] = field(default_factory=dict)
    word_embeddings: dict[tuple[int, int], list[float]] = field(
        default_factory=dict
    )

    _closed: bool = field(default=False, repr=False)

    def wait_for_compaction_to_finish(
        self,
        dynamo_client: "DynamoClient",
        max_wait_seconds: int = 300,
        poll_interval_seconds: int = 5,
    ) -> bool:
        """
        Poll DynamoDB until both lines and words compaction complete.

        Args:
            dynamo_client: DynamoDB client for polling CompactionRun status
            max_wait_seconds: Maximum time to wait (default 5 minutes)
            poll_interval_seconds: Polling interval (default 5 seconds)

        Returns:
            True if both completed successfully, False if timeout or failure
        """
        start_time = time.time()

        while time.time() - start_time < max_wait_seconds:
            run = dynamo_client.get_compaction_run(
                image_id=self.compaction_run.image_id,
                receipt_id=self.compaction_run.receipt_id,
                run_id=self.compaction_run.run_id,
            )

            if run is None:
                logger.warning(
                    "CompactionRun not found: %s", self.compaction_run.run_id
                )
                return False

            lines_done = run.lines_state in (
                CompactionState.COMPLETED.value,
                CompactionState.FAILED.value,
            )
            words_done = run.words_state in (
                CompactionState.COMPLETED.value,
                CompactionState.FAILED.value,
            )

            if lines_done and words_done:
                if (
                    run.lines_state == CompactionState.COMPLETED.value
                    and run.words_state == CompactionState.COMPLETED.value
                ):
                    logger.info(
                        "Compaction completed successfully: %s",
                        self.compaction_run.run_id,
                    )
                    return True
                logger.error(
                    "Compaction failed: lines=%s, words=%s",
                    run.lines_state,
                    run.words_state,
                )
                return False

            time.sleep(poll_interval_seconds)

        logger.warning(
            "Compaction timed out after %d seconds: %s",
            max_wait_seconds,
            self.compaction_run.run_id,
        )
        return False

    def close(self) -> None:
        """
        Close both ChromaClients and clean up temp directories.

        Critical for releasing SQLite file locks (issue #5868).
        """
        if self._closed:
            return

        try:
            self.lines_client.close()
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning("Error closing lines_client: %s", e)

        try:
            self.words_client.close()
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning("Error closing words_client: %s", e)

        # Clean up temp directories
        try:
            if self._lines_dir and os.path.exists(self._lines_dir):
                shutil.rmtree(self._lines_dir, ignore_errors=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning("Error cleaning up lines_dir: %s", e)

        try:
            if self._words_dir and os.path.exists(self._words_dir):
                shutil.rmtree(self._words_dir, ignore_errors=True)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.warning("Error cleaning up words_dir: %s", e)

        self._closed = True
        logger.debug("EmbeddingResult closed")

    def __enter__(self) -> "EmbeddingResult":
        """Context manager entry."""
        return self

    def __exit__(
        self,
        exc_type: Any,
        exc_val: Any,
        exc_tb: Any,
    ) -> None:
        """Context manager exit - ensures cleanup."""
        self.close()


@dataclass
class EmbeddingConfig:
    """Configuration for embedding orchestration.

    Groups parameters for create_embeddings_and_compaction_run to reduce
    function signature complexity.
    """

    image_id: str
    receipt_id: int
    chromadb_bucket: str
    dynamo_client: "DynamoClient"
    s3_client: "S3Client | None" = None
    receipt_place: ReceiptPlace | None = None
    receipt_word_labels: list[ReceiptWordLabel] | None = None
    merchant_name: str | None = None
    sqs_notify: bool = True


def _send_sqs_notification(
    collection: str,
    delta_prefix: str,
    run_id: str,
    vector_count: int,
) -> None:
    """Send SQS notification for async compaction."""
    queue_url_env = f"CHROMADB_{collection.upper()}_QUEUE_URL"
    queue_url = os.environ.get(queue_url_env)

    if not queue_url:
        logger.debug("SQS queue URL not set for %s, skipping", collection)
        return

    try:
        sqs = boto3.client("sqs")
        message_body = {
            "delta_key": delta_prefix,
            "collection": collection,
            "database": collection,
            "vector_count": vector_count,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "batch_id": run_id,
        }

        # Provide stable group/dedup for FIFO queues
        message_group_id = f"{collection}:{run_id}"
        message_dedup_id = f"{collection}:{run_id}:{delta_prefix}"

        sqs.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(message_body),
            MessageGroupId=message_group_id,
            MessageDeduplicationId=message_dedup_id,
            MessageAttributes={
                "collection": {
                    "StringValue": collection,
                    "DataType": "String",
                },
                "batch_id": {
                    "StringValue": run_id,
                    "DataType": "String",
                },
            },
        )
        logger.info("Sent SQS notification for %s: %s", collection, run_id)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.warning("Failed to send SQS notification: %s", e)


def _upload_deltas(
    line_payload: dict[str, Any],
    word_payload: dict[str, Any],
    run_id: str,
    *,
    chromadb_bucket: str,
    s3_client: "S3Client",
    sqs_notify: bool,
) -> tuple[str, str]:
    """Create delta ChromaDB collections and upload to S3.

    Returns:
        Tuple of (lines_prefix, words_prefix) for the uploaded deltas.
    """
    delta_lines_dir = tempfile.mkdtemp(prefix="lines_delta_")
    delta_words_dir = tempfile.mkdtemp(prefix="words_delta_")

    try:
        # Create delta-only clients and upsert
        delta_line_client = ChromaClient(
            persist_directory=delta_lines_dir,
            mode="delta",
            metadata_only=True,
        )
        delta_word_client = ChromaClient(
            persist_directory=delta_words_dir,
            mode="delta",
            metadata_only=True,
        )

        delta_line_client.upsert_vectors(
            collection_name="lines", **line_payload
        )
        delta_word_client.upsert_vectors(
            collection_name="words", **word_payload
        )

        # Close before upload (critical for file locking)
        delta_line_client.close()
        delta_word_client.close()

        # Upload deltas to S3
        lines_prefix = f"lines/delta/{run_id}"
        words_prefix = f"words/delta/{run_id}"

        lines_upload = upload_delta_tarball(
            local_delta_dir=delta_lines_dir,
            bucket=chromadb_bucket,
            delta_prefix=lines_prefix,
            metadata={"delta_key": lines_prefix, "run_id": run_id},
            s3_client=s3_client,
        )
        if lines_upload.get("status") != "uploaded":
            raise RuntimeError(f"Failed to upload lines delta: {lines_upload}")

        words_upload = upload_delta_tarball(
            local_delta_dir=delta_words_dir,
            bucket=chromadb_bucket,
            delta_prefix=words_prefix,
            metadata={"delta_key": words_prefix, "run_id": run_id},
            s3_client=s3_client,
        )
        if words_upload.get("status") != "uploaded":
            raise RuntimeError(f"Failed to upload words delta: {words_upload}")

        logger.info(
            "Uploaded deltas to S3: lines=%s, words=%s",
            lines_upload.get("object_key"),
            words_upload.get("object_key"),
        )

        # Send SQS notifications if enabled
        if sqs_notify:
            _send_sqs_notification(
                collection="lines",
                delta_prefix=lines_prefix,
                run_id=run_id,
                vector_count=len(line_payload["ids"]),
            )
            _send_sqs_notification(
                collection="words",
                delta_prefix=words_prefix,
                run_id=run_id,
                vector_count=len(word_payload["ids"]),
            )

        return lines_prefix, words_prefix

    finally:
        shutil.rmtree(delta_lines_dir, ignore_errors=True)
        shutil.rmtree(delta_words_dir, ignore_errors=True)


def create_embeddings_and_compaction_run(
    receipt_lines: list[ReceiptLine],
    receipt_words: list[ReceiptWord],
    config: EmbeddingConfig,
) -> EmbeddingResult:
    """
    Create embeddings, upload deltas to S3, and return local clients.

    This is the main orchestration function for embedding creation. It:
    1. Downloads snapshots and generates embeddings in PARALLEL (4 concurrent ops)
    2. Builds payloads and caches from embeddings
    3. Upserts to local ChromaDB clients
    4. Uploads deltas to S3 (triggering async compaction via SQS)
    5. Creates and persists CompactionRun to DynamoDB
    6. Returns EmbeddingResult with local ChromaClients for immediate querying

    Args:
        receipt_lines: Lines to embed (required)
        receipt_words: Words to embed (required)
        config: EmbeddingConfig with image_id, receipt_id, bucket, etc.

    Returns:
        EmbeddingResult with lines_client, words_client, and compaction_run

    Raises:
        ValueError: If receipt_lines or receipt_words is empty
        RuntimeError: If OpenAI API key is not set
    """
    # Validate inputs
    if not receipt_lines:
        raise ValueError("receipt_lines cannot be empty")
    if not receipt_words:
        raise ValueError("receipt_words cannot be empty")
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY environment variable not set")

    openai_client = OpenAI()

    # Resolve merchant name from receipt_place if not explicitly provided
    merchant_name = config.merchant_name or (
        config.receipt_place.merchant_name if config.receipt_place else None
    )

    run_id = str(uuid.uuid4())
    s3_client = config.s3_client or boto3.client("s3")
    model = os.environ.get("OPENAI_EMBEDDING_MODEL", EMBEDDING_MODEL)

    # Step 1: Download snapshots + generate embeddings in PARALLEL
    # This runs 4 I/O operations concurrently for significant speedup
    logger.info(
        "Starting parallel download + embedding (4 concurrent operations)"
    )
    local_lines_dir, local_words_dir, line_embeddings_list, word_embeddings_list = (
        _download_and_embed_parallel(
            receipt_lines=receipt_lines,
            receipt_words=receipt_words,
            chromadb_bucket=config.chromadb_bucket,
            s3_client=s3_client,
            openai_client=openai_client,
            model=model,
        )
    )
    logger.info(
        "Parallel operations complete: lines_dir=%s, words_dir=%s",
        local_lines_dir,
        local_words_dir,
    )

    try:
        # Step 2: Build payloads from embeddings
        line_records = [
            LineEmbeddingRecord(line=ln, embedding=emb)
            for ln, emb in zip(receipt_lines, line_embeddings_list, strict=True)
        ]
        line_payload = build_line_payload(
            line_records,
            receipt_lines,
            receipt_words,
            merchant_name=merchant_name,
        )

        # Build line embedding cache for reuse in merchant resolution
        line_embedding_cache: dict[int, list[float]] = {
            ln.line_id: emb
            for ln, emb in zip(receipt_lines, line_embeddings_list, strict=True)
        }

        word_records = [
            WordEmbeddingRecord(word=w, embedding=emb)
            for w, emb in zip(receipt_words, word_embeddings_list, strict=True)
        ]
        word_payload = build_word_payload(
            word_records,
            receipt_words,
            config.receipt_word_labels or [],
            merchant_name=merchant_name,
        )

        # Build word embedding cache for reuse in label validation
        word_embedding_cache: dict[tuple[int, int], list[float]] = {
            (w.line_id, w.word_id): emb
            for w, emb in zip(receipt_words, word_embeddings_list, strict=True)
        }

        # Step 3: Create local ChromaClients on downloaded snapshots and upsert
        lines_client = ChromaClient(
            persist_directory=local_lines_dir,
            mode="write",
            metadata_only=True,
        )
        words_client = ChromaClient(
            persist_directory=local_words_dir,
            mode="write",
            metadata_only=True,
        )

        lines_client.upsert_vectors(collection_name="lines", **line_payload)
        words_client.upsert_vectors(collection_name="words", **word_payload)

        logger.info(
            "Upserted embeddings locally: lines=%d, words=%d",
            len(line_payload["ids"]),
            len(word_payload["ids"]),
        )

        # Step 4: Upload deltas to S3
        lines_prefix, words_prefix = _upload_deltas(
            line_payload,
            word_payload,
            run_id,
            chromadb_bucket=config.chromadb_bucket,
            s3_client=s3_client,
            sqs_notify=config.sqs_notify,
        )

        # Step 5: Create and persist CompactionRun
        compaction_run = CompactionRun(
            run_id=run_id,
            image_id=config.image_id,
            receipt_id=config.receipt_id,
            lines_delta_prefix=f"{lines_prefix}/",
            words_delta_prefix=f"{words_prefix}/",
        )

        config.dynamo_client.add_compaction_run(compaction_run)
        logger.info(
            "Created CompactionRun %s for receipt %s",
            run_id,
            config.receipt_id,
        )

        # Step 6: Return EmbeddingResult with embedding caches
        return EmbeddingResult(
            lines_client=lines_client,
            words_client=words_client,
            compaction_run=compaction_run,
            line_embeddings=line_embedding_cache,
            word_embeddings=word_embedding_cache,
            _lines_dir=local_lines_dir,
            _words_dir=local_words_dir,
        )

    except Exception:
        shutil.rmtree(local_lines_dir, ignore_errors=True)
        shutil.rmtree(local_words_dir, ignore_errors=True)
        raise
