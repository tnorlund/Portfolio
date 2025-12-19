"""
High-level embedding orchestration for receipt processing.

Provides EmbeddingResult class and create_embeddings_and_compaction_run function
that encapsulates the complete embedding workflow:
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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, List, Optional

from receipt_chroma.data.chroma_client import ChromaClient
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
    ReceiptMetadata,
    ReceiptPlace,
    ReceiptWord,
    ReceiptWordLabel,
)

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

    from receipt_dynamo import DynamoClient

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "text-embedding-3-small"


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

    # Private fields for temp directory cleanup
    _lines_dir: str = field(repr=False)
    _words_dir: str = field(repr=False)
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


def _send_sqs_notification(
    collection: str,
    delta_prefix: str,
    run_id: str,
    vector_count: int,
) -> None:
    """Send SQS notification for async compaction."""
    import boto3

    queue_url_env = f"CHROMADB_{collection.upper()}_QUEUE_URL"
    queue_url = os.environ.get(queue_url_env)

    if not queue_url:
        logger.debug("SQS queue URL not set for %s, skipping", collection)
        return

    try:
        sqs = boto3.client("sqs")  # pylint: disable=import-outside-toplevel
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


def create_embeddings_and_compaction_run(
    receipt_lines: List[ReceiptLine],
    receipt_words: List[ReceiptWord],
    image_id: str,
    receipt_id: int,
    chromadb_bucket: str,
    dynamo_client: "DynamoClient",
    s3_client: Optional["S3Client"] = None,
    receipt_metadata: Optional[ReceiptMetadata] = None,
    receipt_place: Optional[ReceiptPlace] = None,
    receipt_word_labels: Optional[List[ReceiptWordLabel]] = None,
    merchant_name: Optional[str] = None,
    sqs_notify: bool = True,
) -> EmbeddingResult:
    """
    Create embeddings, upload deltas to S3, and return local clients.

    This is the main orchestration function for embedding creation. It:
    1. Downloads current snapshots from S3 (or initializes empty if none exist)
    2. Generates embeddings via OpenAI
    3. Creates deltas and upserts them locally (snapshot + delta merged)
    4. Uploads deltas to S3 (triggering async compaction via SQS)
    5. Creates and persists CompactionRun to DynamoDB
    6. Returns EmbeddingResult with local ChromaClients for immediate querying

    Args:
        receipt_lines: Lines to embed (required)
        receipt_words: Words to embed (required)
        image_id: Image identifier
        receipt_id: Receipt identifier
        chromadb_bucket: S3 bucket for ChromaDB snapshots/deltas
        dynamo_client: DynamoDB client for CompactionRun persistence
        s3_client: Optional S3 client (creates one if not provided)
        receipt_metadata: Optional legacy metadata for merchant context (deprecated)
        receipt_place: Optional place data for merchant context (preferred)
        receipt_word_labels: Optional word labels for enrichment
        merchant_name: Explicit merchant name override
        sqs_notify: Whether to send SQS notification (default True)

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

    # Import OpenAI client lazily
    from openai import (  # pylint: disable=import-outside-toplevel
        OpenAI,
    )

    openai_client = OpenAI()

    # Resolve merchant name (prefer receipt_place over legacy receipt_metadata)
    merchant_name = merchant_name or (
        receipt_place.merchant_name if receipt_place else (
            receipt_metadata.merchant_name if receipt_metadata else None
        )
    )

    run_id = str(uuid.uuid4())

    # Create S3 client if not provided
    if s3_client is None:
        import boto3  # pylint: disable=import-outside-toplevel

        s3_client = boto3.client("s3")

    # Step 1: Download snapshots to local directories
    local_lines_dir = tempfile.mkdtemp(prefix="lines_snapshot_")
    local_words_dir = tempfile.mkdtemp(prefix="words_snapshot_")

    try:
        # Download lines snapshot
        lines_download = download_snapshot_atomic(
            bucket=chromadb_bucket,
            collection="lines",
            local_path=local_lines_dir,
            verify_integrity=False,  # Skip for speed
            s3_client=s3_client,
        )
        logger.info(
            "Downloaded lines snapshot: status=%s, version=%s",
            lines_download.get("status"),
            lines_download.get("version_id"),
        )

        # Download words snapshot
        words_download = download_snapshot_atomic(
            bucket=chromadb_bucket,
            collection="words",
            local_path=local_words_dir,
            verify_integrity=False,
            s3_client=s3_client,
        )
        logger.info(
            "Downloaded words snapshot: status=%s, version=%s",
            words_download.get("status"),
            words_download.get("version_id"),
        )

        # Step 2: Generate embeddings via OpenAI
        model = os.environ.get("OPENAI_EMBEDDING_MODEL", EMBEDDING_MODEL)

        line_embeddings = embed_texts(
            client=openai_client,
            texts=[ln.text for ln in receipt_lines],
            model=model,
        )
        line_records = [
            LineEmbeddingRecord(line=ln, embedding=emb)
            for ln, emb in zip(receipt_lines, line_embeddings, strict=True)
        ]
        line_payload = build_line_payload(
            line_records,
            receipt_lines,
            receipt_words,
            merchant_name=merchant_name,
        )

        word_embeddings = embed_texts(
            client=openai_client,
            texts=[w.text for w in receipt_words],
            model=model,
        )
        word_records = [
            WordEmbeddingRecord(word=w, embedding=emb)
            for w, emb in zip(receipt_words, word_embeddings, strict=True)
        ]
        word_payload = build_word_payload(
            word_records,
            receipt_words,
            receipt_word_labels or [],
            merchant_name=merchant_name,
        )

        # Step 3: Create local ChromaClients on downloaded snapshots and upsert
        # These operate on the downloaded snapshots, adding the new embeddings
        lines_client = ChromaClient(
            persist_directory=local_lines_dir,
            mode="write",  # Write mode to allow upserts
            metadata_only=True,  # Avoid OpenAI API costs
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

        # Step 4: Create separate delta directories and upload to S3
        delta_lines_dir = tempfile.mkdtemp(prefix="lines_delta_")
        delta_words_dir = tempfile.mkdtemp(prefix="words_delta_")

        try:
            # Create delta-only clients
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

            # Close delta clients before upload (critical for file locking)
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
                raise RuntimeError(
                    f"Failed to upload lines delta: {lines_upload}"
                )

            words_upload = upload_delta_tarball(
                local_delta_dir=delta_words_dir,
                bucket=chromadb_bucket,
                delta_prefix=words_prefix,
                metadata={"delta_key": words_prefix, "run_id": run_id},
                s3_client=s3_client,
            )
            if words_upload.get("status") != "uploaded":
                raise RuntimeError(
                    f"Failed to upload words delta: {words_upload}"
                )

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

        finally:
            # Clean up delta directories
            shutil.rmtree(delta_lines_dir, ignore_errors=True)
            shutil.rmtree(delta_words_dir, ignore_errors=True)

        # Step 5: Create and persist CompactionRun
        compaction_run = CompactionRun(
            run_id=run_id,
            image_id=image_id,
            receipt_id=receipt_id,
            lines_delta_prefix=f"{lines_prefix}/",
            words_delta_prefix=f"{words_prefix}/",
        )

        dynamo_client.add_compaction_run(compaction_run)
        logger.info(
            "Created CompactionRun %s for receipt %s",
            run_id,
            receipt_id,
        )

        # Step 6: Return EmbeddingResult
        return EmbeddingResult(
            lines_client=lines_client,
            words_client=words_client,
            compaction_run=compaction_run,
            _lines_dir=local_lines_dir,
            _words_dir=local_words_dir,
        )

    except Exception:  # pylint: disable=broad-exception-caught
        # Clean up on failure
        shutil.rmtree(local_lines_dir, ignore_errors=True)
        shutil.rmtree(local_words_dir, ignore_errors=True)
        raise
