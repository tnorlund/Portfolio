import json
import os
import tempfile
import uuid
from typing import Any, Dict, Iterator

import boto3
import logging
from botocore.exceptions import ClientError, BotoCoreError

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


logger = logging.getLogger()
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")


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
        except Exception as id_err:
            logger.error("Failed to get STS caller identity: %s", id_err)
        raise
    for raw in obj["Body"].iter_lines():
        if not raw:
            continue
        yield json.loads(raw.decode("utf-8"))


def _rehydrate(lines_rows, words_rows):
    # Rehydrate to real entity classes so geometry methods exist
    lines = [ReceiptLine(**r) for r in lines_rows]
    words = [ReceiptWord(**r) for r in words_rows]
    return lines, words


def lambda_handler(event: Dict[str, Any], _ctx: Any):
    logger.info(
        "embed_from_ndjson invoked with event keys: %s",
        list(event.keys()) if isinstance(event, dict) else type(event),
    )
    if isinstance(event, dict) and event.get("Records"):
        results = []
        for rec in event.get("Records", []):
            msg = (
                json.loads(rec["body"])
                if isinstance(rec.get("body"), str)
                else rec.get("body")
            )
            logger.info(
                "Processing SQS record payload: %s",
                {
                    k: msg.get(k)
                    for k in [
                        "image_id",
                        "receipt_id",
                        "artifacts_bucket",
                        "lines_key",
                        "words_key",
                    ]
                },
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

    chroma_bucket = os.environ.get("CHROMADB_BUCKET", "")
    dynamo_table = os.environ["DYNAMO_TABLE_NAME"]

    logger.info(
        "Start embedding for image_id=%s receipt_id=%s artifacts_bucket=%s lines_key=%s words_key=%s chroma_bucket=%s table=%s",
        image_id,
        receipt_id,
        artifacts_bucket,
        lines_key,
        words_key,
        chroma_bucket,
        dynamo_table,
    )

    if not chroma_bucket:
        logger.error(
            "CHROMADB_BUCKET environment variable is not set; cannot upload deltas"
        )
        raise RuntimeError("CHROMADB_BUCKET is not set")

    lines_rows = list(_iter_ndjson(artifacts_bucket, lines_key))
    words_rows = list(_iter_ndjson(artifacts_bucket, words_key))
    lines, words = _rehydrate(lines_rows, words_rows)
    logger.info(
        "Loaded NDJSON rows: lines=%d words=%d",
        len(lines_rows),
        len(words_rows),
    )

    # Resolve merchant before embedding
    merchant_name = None
    try:
        dynamo = DynamoClient(dynamo_table)
        places_api = PlacesAPI(api_key=os.environ.get("GOOGLE_PLACES_API_KEY"))
        chroma_http = os.environ.get("CHROMA_HTTP_ENDPOINT") or os.environ.get(
            "CHROMA_HTTP_URL"
        )

        chroma_line_client = None
        if chroma_http:
            try:
                chroma_line_client = VectorClient.create_chromadb_client(
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

    run_id = str(uuid.uuid4())
    delta_lines_dir = os.path.join(tempfile.gettempdir(), f"lines_{run_id}")
    delta_words_dir = os.path.join(tempfile.gettempdir(), f"words_{run_id}")

    line_client = VectorClient.create_chromadb_client(
        persist_directory=delta_lines_dir, mode="delta", metadata_only=True
    )
    word_client = VectorClient.create_chromadb_client(
        persist_directory=delta_words_dir, mode="delta", metadata_only=True
    )

    upsert_embeddings(
        line_client=line_client,
        word_client=word_client,
        line_embed_fn=embed_lines_realtime,
        word_embed_fn=embed_words_realtime,
        ctx={"lines": lines, "words": words},
        merchant_name=merchant_name,
    )

    lines_prefix = f"lines/delta/{run_id}/"
    words_prefix = f"words/delta/{run_id}/"
    logger.info(
        "Uploading line delta to s3://%s/%s", chroma_bucket, lines_prefix
    )
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
    logger.info(
        "Uploading word delta to s3://%s/%s", chroma_bucket, words_prefix
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

    result = {
        "run_id": run_id,
        "lines_prefix": lines_prefix,
        "words_prefix": words_prefix,
    }
    logger.info("Completed embed_from_ndjson: %s", result)
    return result
