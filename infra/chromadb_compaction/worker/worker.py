import json
import os
import time
import logging
import tempfile
import uuid
from typing import Optional, Any, Dict, Iterator

import boto3
from botocore.exceptions import ClientError, BotoCoreError

# Reuse the same libraries as EmbedFromNdjson
from receipt_label.vector_store import VectorClient
from receipt_label.merchant_resolution.embeddings import upsert_embeddings
from receipt_label.embedding.line.realtime import embed_lines_realtime
from receipt_label.embedding.word.realtime import embed_words_realtime
from receipt_label.utils.chroma_s3_helpers import upload_bundled_delta_to_s3
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities.compaction_run import CompactionRun
from receipt_label.merchant_resolution.resolver import resolve_receipt
from receipt_label.data.places_api import PlacesAPI
from receipt_dynamo.entities.receipt_line import ReceiptLine
from receipt_dynamo.entities.receipt_word import ReceiptWord

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
)
logger = logging.getLogger("compaction-worker")
# Honor LOG_LEVEL if provided
_lvl = os.environ.get("LOG_LEVEL", "INFO").upper()
logger.setLevel(getattr(logging, _lvl, logging.INFO))
# Quiet chatty libs by default
logging.getLogger("boto3").setLevel(logging.WARNING)
logging.getLogger("botocore").setLevel(logging.WARNING)

SLEEP_SECONDS = float(os.environ.get("POLL_INTERVAL_SECONDS", "1.0"))
MAX_MESSAGES = int(os.environ.get("MAX_MESSAGES", "10"))
WAIT_TIME = int(os.environ.get("WAIT_TIME", "10"))

LINES_QUEUE_URL = os.environ.get("LINES_QUEUE_URL")
WORDS_QUEUE_URL = os.environ.get("WORDS_QUEUE_URL")
EMBED_NDJSON_QUEUE_URL = os.environ.get("EMBED_NDJSON_QUEUE_URL")
CHROMA_ROOT = os.environ.get("CHROMA_ROOT", "/mnt/chroma")
DYNAMO_TABLE_NAME = os.environ.get("DYNAMODB_TABLE_NAME")
CHROMADB_BUCKET = os.environ.get("CHROMADB_BUCKET")


sqs = boto3.client("sqs")
s3 = boto3.client("s3")


def receive_batch(queue_url: str):
    return sqs.receive_message(
        QueueUrl=queue_url,
        MaxNumberOfMessages=min(10, MAX_MESSAGES),
        WaitTimeSeconds=min(20, WAIT_TIME),
        MessageAttributeNames=["All"],
    )


def _iter_ndjson(bucket: str, key: str) -> Iterator[Dict[str, Any]]:
    logger.info("Reading NDJSON from s3://%s/%s", bucket, key)
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
    except (ClientError, BotoCoreError) as e:
        logger.error("S3 get_object failed for s3://%s/%s: %s", bucket, key, e)
        try:
            sts = boto3.client("sts")
            ident = sts.get_caller_identity()
            logger.error(
                "Caller identity: account=%s arn=%s userId=%s",
                ident.get("Account"),
                ident.get("Arn"),
                ident.get("UserId"),
            )
        except (ClientError, BotoCoreError) as id_err:
            logger.error("Failed to get STS caller identity: %s", id_err)
        raise
    for raw in obj["Body"].iter_lines():
        if not raw:
            continue
        yield json.loads(raw.decode("utf-8"))


def _dir_has_content(path: str) -> bool:
    try:
        return os.path.isdir(path) and any(True for _ in os.scandir(path))
    except FileNotFoundError:
        return False


def _restore_snapshot_from_s3(collection: str, dest_dir: str) -> bool:
    if not CHROMADB_BUCKET:
        logger.error("CHROMADB_BUCKET not set; cannot restore snapshots")
        return False
    prefix = f"snapshots/{collection}/"
    logger.info(
        "Restoring snapshot for %s from s3://%s/%s to %s",
        collection,
        CHROMADB_BUCKET,
        prefix,
        dest_dir,
    )
    os.makedirs(dest_dir, exist_ok=True)
    try:
        paginator = s3.get_paginator("list_objects_v2")
        found_any = False
        for page in paginator.paginate(Bucket=CHROMADB_BUCKET, Prefix=prefix):
            for obj in page.get("Contents", []) or []:
                key = obj.get("Key")
                if not key or key.endswith("/"):
                    continue
                rel = key[len(prefix) :]
                local_path = os.path.join(dest_dir, rel)
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                s3.download_file(CHROMADB_BUCKET, key, local_path)
                found_any = True
        if not found_any:
            logger.warning(
                "No snapshot objects found at s3://%s/%s",
                CHROMADB_BUCKET,
                prefix,
            )
        else:
            logger.info(
                "Snapshot restore completed for %s into %s",
                collection,
                dest_dir,
            )
        return found_any
    except (ClientError, BotoCoreError) as e:
        logger.error("Snapshot restore failed: %s", e)
        return False


def _rehydrate(
    lines_rows, words_rows
) -> tuple[list[ReceiptLine], list[ReceiptWord]]:
    # Rehydrate to real entity classes so geometry methods exist
    lines = [ReceiptLine(**r) for r in (lines_rows or [])]
    words = [ReceiptWord(**r) for r in (words_rows or [])]
    return lines, words


def _process_embed_from_ndjson(payload: Dict[str, Any]) -> Dict[str, Any]:
    image_id = payload["image_id"]
    receipt_id = int(payload["receipt_id"])  # required
    artifacts_bucket = payload["artifacts_bucket"]
    lines_key = payload["lines_key"]
    words_key = payload["words_key"]

    if not DYNAMO_TABLE_NAME:
        raise RuntimeError("DYNAMODB_TABLE_NAME is not set")
    if not CHROMADB_BUCKET:
        logger.error(
            "CHROMADB_BUCKET environment variable is not set; cannot upload deltas"
        )
        raise RuntimeError("CHROMADB_BUCKET is not set")

    # Read NDJSON artifacts and rehydrate
    lines_rows = list(_iter_ndjson(artifacts_bucket, lines_key))
    words_rows = list(_iter_ndjson(artifacts_bucket, words_key))
    lines, words = _rehydrate(lines_rows, words_rows)
    logger.info(
        "Loaded NDJSON rows: lines=%d words=%d",
        len(lines_rows),
        len(words_rows),
    )

    # Optional merchant resolution to enrich embeddings
    merchant_name = None
    try:
        dynamo = DynamoClient(DYNAMO_TABLE_NAME)
        places_api = PlacesAPI(api_key=os.environ.get("GOOGLE_PLACES_API_KEY"))

        # Ensure EFS-backed Chroma exists (snapshot-aware bootstrap)
        lines_dir = os.path.join(CHROMA_ROOT, "lines")
        words_dir = os.path.join(CHROMA_ROOT, "words")
        if not _dir_has_content(lines_dir):
            logger.warning(
                "EFS lines dir missing or empty; restoring snapshot"
            )
            _restore_snapshot_from_s3("lines", lines_dir)
        if not _dir_has_content(words_dir):
            logger.warning(
                "EFS words dir missing or empty; restoring snapshot"
            )
            _restore_snapshot_from_s3("words", words_dir)

        # Read directly from EFS-backed Chroma persist directory (no HTTP server required)
        chroma_line_client = VectorClient.create_chromadb_client(
            persist_directory=lines_dir, mode="read"
        )
        logger.info(
            "Using EFS-backed Chroma for read-only access",
            extra={"persist_directory": lines_dir},
        )

        def _embed_texts(texts) -> list[list[float]]:
            if not texts:
                return []
            if not os.environ.get("OPENAI_API_KEY"):
                raise RuntimeError("OPENAI_API_KEY is not set")
            from receipt_label.utils import get_client_manager

            openai_client = get_client_manager().openai
            resp = openai_client.embeddings.create(
                model="text-embedding-3-small", input=list(texts)
            )
            logger.info(
                "OpenAI embeddings created",
                extra={"num_embeddings": len(resp.data)},
            )
            return [d.embedding for d in resp.data]

        resolution = resolve_receipt(
            key=(image_id, receipt_id),
            dynamo=dynamo,
            places_api=places_api,
            chroma_line_client=chroma_line_client,
            embed_fn=_embed_texts,
            write_metadata=True,
        )
        best = (resolution.get("decision") or {}).get("best") or {}
        merchant_name = best.get("name")
    except Exception:
        merchant_name = None

    run_id = str(uuid.uuid4())

    # Local delta dirs under ephemeral storage, to be uploaded to S3
    delta_lines_dir = os.path.join(tempfile.gettempdir(), f"lines_{run_id}")
    delta_words_dir = os.path.join(tempfile.gettempdir(), f"words_{run_id}")
    line_client = VectorClient.create_chromadb_client(
        persist_directory=delta_lines_dir, mode="delta", metadata_only=True
    )
    word_client = VectorClient.create_chromadb_client(
        persist_directory=delta_words_dir, mode="delta", metadata_only=True
    )

    # Upsert realtime embeddings into local delta
    upsert_embeddings(
        line_client=line_client,
        word_client=word_client,
        line_embed_fn=embed_lines_realtime,
        word_embed_fn=embed_words_realtime,
        ctx={"lines": lines, "words": words},
        merchant_name=merchant_name,
    )
    logger.info(
        "Upserted embeddings into delta mode (lines=%d, words=%d)",
        len(lines),
        len(words),
    )

    # Upload deltas to S3 and write CompactionRun to DynamoDB
    lines_prefix = f"lines/delta/{run_id}/"
    words_prefix = f"words/delta/{run_id}/"
    logger.info(
        "Uploading line delta to s3://%s/%s", CHROMADB_BUCKET, lines_prefix
    )
    upload_bundled_delta_to_s3(
        local_delta_dir=delta_lines_dir,
        bucket=CHROMADB_BUCKET,
        delta_prefix=lines_prefix,
        metadata={
            "run_id": run_id,
            "image_id": image_id,
            "receipt_id": str(receipt_id),
            "collection": "lines",
        },
    )
    logger.info(
        "Uploaded lines delta bundle to s3://%s/%s",
        CHROMADB_BUCKET,
        lines_prefix,
    )
    logger.info(
        "Uploading word delta to s3://%s/%s", CHROMADB_BUCKET, words_prefix
    )
    upload_bundled_delta_to_s3(
        local_delta_dir=delta_words_dir,
        bucket=CHROMADB_BUCKET,
        delta_prefix=words_prefix,
        metadata={
            "run_id": run_id,
            "image_id": image_id,
            "receipt_id": str(receipt_id),
            "collection": "words",
        },
    )
    logger.info(
        "Uploaded words delta bundle to s3://%s/%s",
        CHROMADB_BUCKET,
        words_prefix,
    )

    dynamo = DynamoClient(DYNAMO_TABLE_NAME)
    dynamo.add_compaction_run(
        CompactionRun(
            run_id=run_id,
            image_id=image_id,
            receipt_id=receipt_id,
            lines_delta_prefix=lines_prefix,
            words_delta_prefix=words_prefix,
        )
    )
    logger.info(
        "Wrote CompactionRun %s for image_id=%s receipt_id=%s",
        run_id,
        image_id,
        receipt_id,
    )

    result = {
        "run_id": run_id,
        "lines_prefix": lines_prefix,
        "words_prefix": words_prefix,
    }
    logger.info("Completed embed_from_ndjson: %s", result)
    return result


def _process_payload_dict(body: dict, queue_name: str) -> None:
    """Process embed-from-ndjson messages from EMBED_NDJSON_QUEUE.

    This worker ONLY consumes from EMBED_NDJSON_QUEUE. It creates embeddings
    from NDJSON artifacts and uploads delta files to S3, then writes a
    CompactionRun record to DynamoDB.

    The enhanced_compaction_handler Lambda consumes LINES/WORDS queues to merge
    those deltas into the main ChromaDB collections.
    """
    # Log the actual body keys for debugging
    logger.info(
        f"Processing message from queue={queue_name} with keys={list(body.keys())}"
    )

    # Validate this is an embed-from-ndjson message
    required_keys = {
        "image_id",
        "receipt_id",
        "artifacts_bucket",
        "lines_key",
        "words_key",
    }
    if not required_keys.issubset(body.keys()):
        logger.error(
            f"Invalid message structure - missing required keys. "
            f"Expected: {required_keys}, Got: {list(body.keys())}"
        )
        raise ValueError(
            f"Message missing required keys: {required_keys - set(body.keys())}"
        )

    logger.info(
        f"Embedding from NDJSON: image_id={body['image_id']}, "
        f"receipt_id={body['receipt_id']}"
    )
    _process_embed_from_ndjson(body)


def handle_message(msg: dict, queue_name: str) -> None:
    body = json.loads(msg.get("Body", "{}"))
    _process_payload_dict(body, queue_name)


def delete_message(queue_url: str, receipt_handle: str) -> None:
    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)
    logger.info("Deleted message from %s", queue_url)


def drain_queue(queue_url: Optional[str], queue_name: str) -> int:
    if not queue_url:
        return 0
    processed = 0
    resp = receive_batch(queue_url)
    msgs = resp.get("Messages", []) or []
    if msgs:
        logger.info("Fetched %d messages from %s", len(msgs), queue_name)
    for msg in msgs:
        try:
            handle_message(msg, queue_name)
            delete_message(queue_url, msg["ReceiptHandle"])
            processed += 1
        except Exception as e:
            logger.exception(
                "Failed processing message",
                extra={"queue": queue_name, "error": str(e)},
            )
    return processed


def main():
    """
    ECS worker main loop - consumes ONLY from EMBED_NDJSON_QUEUE.

    LINES_QUEUE and WORDS_QUEUE are consumed by enhanced_compaction_handler Lambda
    to merge deltas created by this worker.
    """
    if not EMBED_NDJSON_QUEUE_URL:
        logger.error(
            "EMBED_NDJSON_QUEUE_URL not set; worker has no queue to consume"
        )
        raise RuntimeError(
            "EMBED_NDJSON_QUEUE_URL environment variable is required"
        )

    logger.info(
        "Worker starting - consuming from EMBED_NDJSON_QUEUE only",
        extra={
            "chroma_root": CHROMA_ROOT,
            "embed_ndjson_queue_url": EMBED_NDJSON_QUEUE_URL,
        },
    )
    while True:
        # This worker ONLY processes embed-from-ndjson messages
        # LINES/WORDS queues are handled by enhanced_compaction_handler Lambda
        total = drain_queue(EMBED_NDJSON_QUEUE_URL, "embed_ndjson")
        if total == 0:
            time.sleep(SLEEP_SECONDS)


def lambda_handler(event=None, context=None):
    """SQS batch entrypoint for container-based Lambda.

    Expects event["Records"]; processes each record independently.
    Uses ReportBatchItemFailures response format so SQS will retry only failed
    records in the batch.
    """
    failed: list[dict] = []
    records = (
        (event or {}).get("Records", []) if isinstance(event, dict) else []
    )
    for rec in records:
        msg_id = rec.get("messageId")
        try:
            raw_body = rec.get("body", "{}")
            logger.info(f"Raw SQS body (first 500 chars): {raw_body[:500]}")
            body = json.loads(raw_body)

            # Handle potential double-encoding: if body is a string, parse again
            if isinstance(body, str):
                logger.info("Body is a string, attempting second JSON parse")
                body = json.loads(body)

            _process_payload_dict(body, queue_name="sqs")
        except Exception as e:  # pylint: disable=broad-except
            logger.exception(
                "Failed processing SQS record", extra={"error": str(e)}
            )
            if msg_id:
                failed.append({"itemIdentifier": msg_id})
    return {"batchItemFailures": failed}


if __name__ == "__main__":
    main()
