import json
import os
import tempfile
import uuid
from typing import Any, Dict, Iterator

import boto3

from receipt_label.vector_store import VectorClient
from receipt_label.merchant_resolution.embeddings import upsert_embeddings
from receipt_label.embedding.line.realtime import embed_lines_realtime
from receipt_label.embedding.word.realtime import embed_words_realtime
from receipt_label.utils.chroma_s3_helpers import upload_bundled_delta_to_s3
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities.compaction_run import CompactionRun
from receipt_label.merchant_resolution.resolver import resolve_receipt
from receipt_label.data.places_api import PlacesAPI


s3 = boto3.client("s3")


def _iter_ndjson(bucket: str, key: str) -> Iterator[Dict[str, Any]]:
    obj = s3.get_object(Bucket=bucket, Key=key)
    # iter_lines yields bytes; decode to str
    for raw in obj["Body"].iter_lines():
        if not raw:
            continue
        yield json.loads(raw.decode("utf-8"))


def _rehydrate(lines_rows, words_rows):
    # Minimal attribute carriers compatible with embed_* functions
    class L:  # noqa: N801 - simple container
        pass

    class W:  # noqa: N801 - simple container
        pass

    lines, words = [], []
    for r in lines_rows:
        o = L()
        o.image_id = r["image_id"]
        o.receipt_id = int(r["receipt_id"])
        o.line_id = int(r["line_id"])
        o.text = r.get("text", "")
        # geometry fields optional
        o.y_top = r.get("y_top")
        o.y_bottom = r.get("y_bottom")
        o.x_left = r.get("x_left")
        o.x_right = r.get("x_right")
        lines.append(o)

    for r in words_rows:
        o = W()
        o.image_id = r["image_id"]
        o.receipt_id = int(r["receipt_id"])
        o.line_id = int(r["line_id"])
        o.word_id = int(r["word_id"])
        o.text = r.get("text", "")
        o.x = r.get("x")
        o.y = r.get("y")
        o.w = r.get("w")
        o.h = r.get("h")
        words.append(o)

    return lines, words


def handler(event, _ctx):
    # Support both direct invoke and SQS trigger batching
    if isinstance(event, dict) and event.get("Records"):
        # SQS event
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

    # Read NDJSON artifacts
    lines_rows = list(_iter_ndjson(artifacts_bucket, lines_key))
    words_rows = list(_iter_ndjson(artifacts_bucket, words_key))
    lines, words = _rehydrate(lines_rows, words_rows)

    # Resolve merchant before embedding (uses ECS Chroma HTTP + Places)
    merchant_name = None
    try:
        dynamo = DynamoClient(dynamo_table)
        places_api = PlacesAPI(api_key=os.environ.get("GOOGLE_PLACES_API_KEY"))
        chroma_http = os.environ.get("CHROMA_HTTP_ENDPOINT") or os.environ.get(
            "CHROMA_HTTP_URL"
        )

        # Minimal HTTP client wrapper: reuse query_words worker? Here we skip client if not set
        from receipt_label.vector_store import VectorClient as _VC

        chroma_line_client = None
        if chroma_http:
            try:
                chroma_line_client = _VC.create_chromadb_client(
                    mode="read", http_url=chroma_http
                )
            except Exception:
                chroma_line_client = None

        def _embed_texts(texts):
            if not texts:
                return []
            if not os.environ.get("OPENAI_API_KEY"):
                return [[0.0] * 1536 for _ in texts]
            from receipt_label.utils import get_client_manager

            openai_client = get_client_manager().openai
            resp = openai_client.embeddings.create(
                model="text-embedding-3-small", input=list(texts)
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

    # Prepare local delta stores
    run_id = str(uuid.uuid4())
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

    # Upload deltas to S3
    lines_prefix = f"lines/delta/{run_id}/"
    words_prefix = f"words/delta/{run_id}/"
    upload_bundled_delta_to_s3(
        local_delta_dir=delta_lines_dir,
        bucket=chroma_bucket,
        delta_prefix=lines_prefix,
        metadata={
            "run_id": run_id,
            "image_id": image_id,
            "receipt_id": str(receipt_id),
            "collection": "lines",
        },
    )
    upload_bundled_delta_to_s3(
        local_delta_dir=delta_words_dir,
        bucket=chroma_bucket,
        delta_prefix=words_prefix,
        metadata={
            "run_id": run_id,
            "image_id": image_id,
            "receipt_id": str(receipt_id),
            "collection": "words",
        },
    )

    # Insert CompactionRun to trigger compactor via streams
    dynamo = DynamoClient(dynamo_table)
    dynamo.add_compaction_run(
        CompactionRun(
            run_id=run_id,
            image_id=image_id,
            receipt_id=receipt_id,
            lines_delta_prefix=lines_prefix,
            words_delta_prefix=words_prefix,
        )
    )

    return {
        "run_id": run_id,
        "lines_prefix": lines_prefix,
        "words_prefix": words_prefix,
    }
