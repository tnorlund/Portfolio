import json
import os
import uuid
from typing import Any, Dict, Iterator, List

import boto3
from receipt_chroma.embedding.delta.producer import produce_embedding_delta
from receipt_chroma.embedding.openai import embed_texts
from receipt_chroma.embedding.records import (
    LineEmbeddingRecord,
    WordEmbeddingRecord,
    build_line_payload,
    build_word_payload,
)
from receipt_dynamo.data.dynamo_client import DynamoClient

s3 = boto3.client("s3")


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

    lines_delta = produce_embedding_delta(
        ids=line_payload["ids"],
        embeddings=line_payload["embeddings"],
        documents=line_payload["documents"],
        metadatas=line_payload["metadatas"],
        bucket_name=chroma_bucket,
        collection_name="lines",
        database_name="lines",
        sqs_queue_url=lines_queue_url,
        batch_id=batch_id,
    )

    words_delta = produce_embedding_delta(
        ids=word_payload["ids"],
        embeddings=word_payload["embeddings"],
        documents=word_payload["documents"],
        metadatas=word_payload["metadatas"],
        bucket_name=chroma_bucket,
        collection_name="words",
        database_name="words",
        sqs_queue_url=words_queue_url,
        batch_id=batch_id,
    )

    return {
        "run_id": batch_id,
        "lines_delta": lines_delta.get("delta_key"),
        "words_delta": words_delta.get("delta_key"),
    }
