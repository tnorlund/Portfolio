import json
import os
import shutil
import tarfile
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

import boto3
from receipt_chroma import ChromaClient
from receipt_chroma.embedding.openai import embed_texts
from receipt_chroma.embedding.records import (
    LineEmbeddingRecord,
    WordEmbeddingRecord,
    build_line_payload,
    build_word_payload,
)
from receipt_dynamo.data.dynamo_client import DynamoClient

s3 = boto3.client("s3")


def _upload_bundled_delta_to_s3(
    local_delta_dir: str,
    bucket: str,
    delta_prefix: str,
    collection_name: str,
    database_name: str,
    sqs_queue_url: Optional[str],
    batch_id: str,
    vector_count: int,
) -> Dict[str, str]:
    """Tar/gzip a delta directory, upload to S3, optionally notify SQS."""
    tmp_dir = tempfile.mkdtemp()
    try:
        tar_path = os.path.join(tmp_dir, "delta.tar.gz")
        with tarfile.open(tar_path, "w:gz") as tar:
            tar.add(local_delta_dir, arcname=".")

        s3_key = f"{delta_prefix.rstrip('/')}/delta.tar.gz"
        s3.upload_file(
            tar_path,
            bucket,
            s3_key,
            ExtraArgs={
                "Metadata": {"delta_key": delta_prefix},
                "ContentType": "application/gzip",
                "ContentEncoding": "gzip",
            },
        )

        if sqs_queue_url:
            sqs = boto3.client("sqs")
            message_body = {
                "delta_key": delta_prefix,
                "collection": collection_name,
                "database": database_name,
                "vector_count": vector_count,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "batch_id": batch_id,
            }
            message_group_id = f"{collection_name}:{batch_id or 'default'}"
            message_dedup_id = f"{collection_name}:{batch_id}:{s3_key}"
            sqs.send_message(
                QueueUrl=sqs_queue_url,
                MessageBody=json.dumps(message_body),
                MessageGroupId=message_group_id,
                MessageDeduplicationId=message_dedup_id,
                MessageAttributes={
                    "collection": {
                        "StringValue": collection_name,
                        "DataType": "String",
                    },
                    "batch_id": {
                        "StringValue": batch_id or "none",
                        "DataType": "String",
                    },
                },
            )

        return {"status": "uploaded", "delta_key": delta_prefix, "s3_key": s3_key}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _iter_ndjson(bucket: str, key: str) -> Iterator[Dict[str, Any]]:
    obj = s3.get_object(Bucket=bucket, Key=key)
    for raw in obj["Body"].iter_lines():
        if not raw:
            continue
        yield json.loads(raw.decode("utf-8"))


def handler(event, _ctx):
    # Support both direct invoke and SQS trigger batching
    if isinstance(event, dict) and event.get("Records"):
        results = []
        for rec in event.get("Records", []):
            msg = (
                json.loads(rec["body"])
                if isinstance(rec.get("body"), str)
                else rec.get("body")
            )
            results.append(_process_single(msg))
        return {"results": results}

    return _process_single(event)


def _process_single(payload: Dict[str, Any]):
    image_id = payload["image_id"]
    receipt_id = int(payload["receipt_id"])
    artifacts_bucket = payload["artifacts_bucket"]
    lines_key = payload["lines_key"]
    words_key = payload["words_key"]

    chroma_bucket = os.environ["CHROMADB_BUCKET"]
    dynamo_table = os.environ["DYNAMO_TABLE_NAME"]
    lines_queue_url = os.environ.get("CHROMADB_LINES_QUEUE_URL")
    words_queue_url = os.environ.get("CHROMADB_WORDS_QUEUE_URL")

    # Read NDJSON artifacts (may be used for debugging)
    _ = list(_iter_ndjson(artifacts_bucket, lines_key))
    _ = list(_iter_ndjson(artifacts_bucket, words_key))

    dynamo = DynamoClient(dynamo_table)
    lines = dynamo.list_receipt_lines_from_receipt(image_id, receipt_id)
    words = dynamo.list_receipt_words_from_receipt(image_id, receipt_id)
    word_labels, _ = dynamo.list_receipt_word_labels_for_receipt(
        image_id, receipt_id
    )

    line_embeddings = embed_texts(
        client=None,
        [ln.text for ln in lines],
        model=os.environ.get(
            "OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"
        ),
        api_key=os.environ.get("OPENAI_API_KEY"),
    )
    line_records = [
        LineEmbeddingRecord(line=ln, embedding=emb)
        for ln, emb in zip(lines, line_embeddings)
    ]
    line_payload = build_line_payload(
        line_records, lines, words, merchant_name=None
    )

    word_embeddings = embed_texts(
        client=None,
        [w.text for w in words],
        model=os.environ.get(
            "OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"
        ),
        api_key=os.environ.get("OPENAI_API_KEY"),
    )
    word_records = [
        WordEmbeddingRecord(word=w, embedding=emb)
        for w, emb in zip(words, word_embeddings)
    ]
    word_payload = build_word_payload(
        word_records, words, word_labels, merchant_name=None
    )

    batch_id = str(uuid.uuid4())

    delta_lines_dir = tempfile.mkdtemp(prefix="lines_delta_")
    delta_words_dir = tempfile.mkdtemp(prefix="words_delta_")

    try:
        line_client = ChromaClient(
            persist_directory=delta_lines_dir,
            mode="delta",
            metadata_only=True,
        )
        word_client = ChromaClient(
            persist_directory=delta_words_dir,
            mode="delta",
            metadata_only=True,
        )

        line_client.upsert_vectors(collection_name="lines", **line_payload)
        word_client.upsert_vectors(collection_name="words", **word_payload)

        line_client.close()
        word_client.close()

        lines_prefix = f"lines/delta/{batch_id}/"
        words_prefix = f"words/delta/{batch_id}/"

        lines_delta = _upload_bundled_delta_to_s3(
            local_delta_dir=delta_lines_dir,
            bucket=chroma_bucket,
            delta_prefix=lines_prefix,
            collection_name="lines",
            database_name="lines",
            sqs_queue_url=lines_queue_url,
            batch_id=batch_id,
            vector_count=len(line_payload["ids"]),
        )

        words_delta = _upload_bundled_delta_to_s3(
            local_delta_dir=delta_words_dir,
            bucket=chroma_bucket,
            delta_prefix=words_prefix,
            collection_name="words",
            database_name="words",
            sqs_queue_url=words_queue_url,
            batch_id=batch_id,
            vector_count=len(word_payload["ids"]),
        )
    finally:
        shutil.rmtree(delta_lines_dir, ignore_errors=True)
        shutil.rmtree(delta_words_dir, ignore_errors=True)

    return {
        "run_id": batch_id,
        "lines_delta": lines_delta.get("delta_key"),
        "words_delta": words_delta.get("delta_key"),
    }
