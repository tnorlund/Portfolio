"""
Embed receipts from NDJSON artifacts.

This lambda processes embedding requests by reading receipt data from DynamoDB,
generating embeddings, and uploading delta tarballs to S3. SQS notifications
are handled by DynamoDB streams when the CompactionRun record is inserted.
"""

import json
import os
import tempfile
import uuid
from typing import Any, Dict, Iterator

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
from receipt_dynamo.entities import CompactionRun

s3 = boto3.client("s3")


def _iter_ndjson(bucket: str, key: str) -> Iterator[Dict[str, Any]]:
    """Iterate over NDJSON records from S3."""
    obj = s3.get_object(Bucket=bucket, Key=key)
    for raw in obj["Body"].iter_lines():
        if not raw:
            continue
        yield json.loads(raw.decode("utf-8"))


def handler(event, _ctx):
    """Lambda handler supporting direct invoke and SQS trigger batching."""
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
    """Process a single embedding request."""
    image_id = payload["image_id"]
    receipt_id = int(payload["receipt_id"])
    artifacts_bucket = payload["artifacts_bucket"]
    lines_key = payload["lines_key"]
    words_key = payload["words_key"]

    chroma_bucket = os.environ["CHROMADB_BUCKET"]
    dynamo_table = os.environ["DYNAMO_TABLE_NAME"]

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
        texts=[ln.text for ln in lines],
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
        texts=[w.text for w in words],
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

    run_id = str(uuid.uuid4())

    delta_lines_dir = tempfile.mkdtemp(prefix="lines_delta_")
    delta_words_dir = tempfile.mkdtemp(prefix="words_delta_")

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

    lines_prefix = f"lines/delta/{run_id}/"
    words_prefix = f"words/delta/{run_id}/"

    # Upload deltas to S3 (persist_and_upload_delta closes the client)
    lines_delta_key = line_client.persist_and_upload_delta(
        bucket=chroma_bucket,
        s3_prefix=lines_prefix,
    )
    words_delta_key = word_client.persist_and_upload_delta(
        bucket=chroma_bucket,
        s3_prefix=words_prefix,
    )

    # Write CompactionRun to DynamoDB - triggers stream processor for SQS
    dynamo.add_compaction_run(
        CompactionRun(
            run_id=run_id,
            image_id=image_id,
            receipt_id=receipt_id,
            lines_delta_prefix=lines_delta_key,
            words_delta_prefix=words_delta_key,
        )
    )

    return {
        "run_id": run_id,
        "lines_delta": lines_delta_key,
        "words_delta": words_delta_key,
    }
