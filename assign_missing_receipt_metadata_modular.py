#!/usr/bin/env python3
import argparse
import os
from pathlib import Path
import sys
import tempfile
import uuid
import time
import logging

from receipt_dynamo.data._pulumi import load_env, load_secrets
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities.compaction_run import CompactionRun
from receipt_label.data.places_api import PlacesAPI
from receipt_label.vector_store import VectorClient
from receipt_label.utils.chroma_s3_helpers import (
    upload_delta_to_s3,
    upload_bundled_delta_to_s3,
)

from receipt_label.merchant_resolution.resolver import resolve_receipt
from receipt_label.merchant_resolution.embeddings import upsert_embeddings
from receipt_label.merchant_resolution.contexts import load_receipt_context

from receipt_label.embedding.line.realtime import embed_lines_realtime
from receipt_label.embedding.word.realtime import embed_words_realtime


def _embed_fn_from_openai_texts(texts):
    """Embedding adapter using OpenAI embeddings; falls back to zeros if key missing."""
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


def main():
    parser = argparse.ArgumentParser(
        description="Resolve a receipt to a merchant (modular)"
    )
    parser.add_argument("image_id", type=str)
    parser.add_argument("receipt_id", type=int)
    parser.add_argument(
        "--env", type=str, default="dev", choices=["dev", "prod"]
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        help="Logging level: DEBUG, INFO, WARNING, ERROR",
    )
    parser.add_argument(
        "--write", action="store_true", help="Write ReceiptMetadata to Dynamo"
    )
    parser.add_argument(
        "--upsert-embeddings",
        action="store_true",
        help="Upsert line/word embeddings to Chroma",
    )
    parser.add_argument(
        "--lines-db",
        type=str,
        default=str((Path(__file__).parent / "dev.chroma_lines").resolve()),
    )
    parser.add_argument(
        "--words-db",
        type=str,
        default=str((Path(__file__).parent / "dev.chroma_words").resolve()),
    )
    # Delta DB paths are generated per-run for safety; no CLI flags
    args = parser.parse_args()

    # Configure logging
    level = getattr(logging, str(args.log_level).upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger = logging.getLogger(__name__)

    logger.info(
        "start assign_missing_receipt_metadata_modular",
    )
    logger.info(
        "params image_id=%s receipt_id=%s env=%s write=%s upsert_embeddings=%s",
        args.image_id,
        args.receipt_id,
        args.env,
        args.write,
        args.upsert_embeddings,
    )

    # Ensure package imports work when running from repo root
    ROOT = Path(__file__).parent
    sys.path.insert(0, str(ROOT / "receipt_label"))

    # Load env/secrets for Places key and Dynamo table
    infra_dir = Path(__file__).parent / "infra"
    t_env = time.time()
    original_cwd = os.getcwd()
    if infra_dir.exists():
        os.chdir(infra_dir)
    try:
        env = load_env(args.env)
        secrets = load_secrets(args.env)
    finally:
        os.chdir(original_cwd)
    logger.info(
        "phase env_loaded duration_ms=%d", int((time.time() - t_env) * 1000)
    )

    table = env.get("dynamodb_table_name")
    if not table:
        raise ValueError("Missing dynamodb_table_name in env")

    os.environ["DYNAMODB_TABLE_NAME"] = table

    t_clients = time.time()
    dynamo = DynamoClient(table)

    # Places API
    google_places_config = secrets.get("portfolio:GOOGLE_PLACES_API_KEY")
    google_places_key = (
        google_places_config.get("value") if google_places_config else None
    ) or os.environ.get("GOOGLE_PLACES_API_KEY")
    places_api = PlacesAPI(api_key=google_places_key)

    # Chroma clients: read-only for queries; write/delta clients for upserts to separate delta DBs
    t_chroma = time.time()
    read_line_client = VectorClient.create_line_client(
        persist_directory=args.lines_db, mode="read"
    )
    delta_lines_db = os.path.join(
        tempfile.gettempdir(), f"chroma_lines_delta_{uuid.uuid4().hex}"
    )
    delta_words_db = os.path.join(
        tempfile.gettempdir(), f"chroma_words_delta_{uuid.uuid4().hex}"
    )
    write_line_client = VectorClient.create_chromadb_client(
        persist_directory=delta_lines_db, mode="delta", metadata_only=True
    )
    write_word_client = VectorClient.create_chromadb_client(
        persist_directory=delta_words_db, mode="delta", metadata_only=True
    )
    logger.info(
        "phase clients_initialized duration_ms=%d lines_db=%s delta_lines=%s delta_words=%s",
        int((time.time() - t_clients) * 1000),
        args.lines_db,
        delta_lines_db,
        delta_words_db,
    )
    logger.info(
        "phase chroma_clients_initialized duration_ms=%d",
        int((time.time() - t_chroma) * 1000),
    )

    key = (args.image_id, int(args.receipt_id))

    t_resolve = time.time()
    result = resolve_receipt(
        key=key,
        dynamo=dynamo,
        places_api=places_api,
        chroma_line_client=read_line_client,
        embed_fn=_embed_fn_from_openai_texts,
        write_metadata=args.write,
    )
    logger.info(
        "phase resolve_receipt duration_ms=%d wrote_metadata=%s best_source=%s best_score=%s",
        int((time.time() - t_resolve) * 1000),
        bool(result.get("wrote_metadata")),
        (result.get("decision", {}).get("best", {}) or {}).get("source"),
        (result.get("decision", {}).get("best", {}) or {}).get("score"),
    )

    # Optional embedding upsert for lines/words
    if args.upsert_embeddings:
        t_ctx = time.time()
        ctx = load_receipt_context(dynamo, key)
        logger.info(
            "phase load_receipt_context duration_ms=%d lines=%d words=%d phones=%d",
            int((time.time() - t_ctx) * 1000),
            len(ctx.get("lines") or []),
            len(ctx.get("words") or []),
            len(ctx.get("phones") or []),
        )
        # Prefer Places merchant name if available among candidates
        merchant_name = None
        for c in result.get("decision", {}).get("candidates", []) or []:
            if c.get("source") == "places" and c.get("name"):
                merchant_name = c.get("name")
                break
        t_upsert = time.time()
        upsert_embeddings(
            line_client=write_line_client,
            word_client=write_word_client,
            line_embed_fn=embed_lines_realtime,
            word_embed_fn=embed_words_realtime,
            ctx=ctx,
            merchant_name=merchant_name,
        )
        logger.info(
            "phase upsert_embeddings duration_ms=%d merchant_name=%s",
            int((time.time() - t_upsert) * 1000),
            merchant_name or "",
        )

        # Upload deltas to S3 and create CompactionRun for stream-triggered merge
        # Resolve Chroma bucket from Pulumi outputs or env
        chroma_bucket = (
            env.get("chromadb_bucket_name")
            or env.get("embedding_chromadb_bucket_name")
            or os.environ.get("CHROMADB_BUCKET")
        )
        if not chroma_bucket:
            raise ValueError(
                "Missing CHROMADB bucket (pulumi output 'chromadb_bucket_name' or env CHROMADB_BUCKET)"
            )

        # Use canonical UUIDv4 string (with dashes) to satisfy CompactionRun validation
        run_id = str(uuid.uuid4())
        lines_delta_prefix = f"lines/delta/{run_id}/"
        words_delta_prefix = f"words/delta/{run_id}/"

        # Upload local delta directories (parallel lines/words uploads)
        from concurrent.futures import ThreadPoolExecutor, as_completed

        t_upload = time.time()

        def _upload_args(local_path: str, key: str, collection: str):
            return dict(
                local_delta_dir=local_path,
                bucket=chroma_bucket,
                delta_prefix=key,
                metadata={
                    "run_id": run_id,
                    "image_id": args.image_id,
                    "receipt_id": str(args.receipt_id),
                    "collection": collection,
                },
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    upload_bundled_delta_to_s3,
                    **_upload_args(
                        delta_lines_db, lines_delta_prefix, "lines"
                    ),
                ),
                executor.submit(
                    upload_bundled_delta_to_s3,
                    **_upload_args(
                        delta_words_db, words_delta_prefix, "words"
                    ),
                ),
            ]
            results = [f.result() for f in as_completed(futures)]
        logger.info(
            "phase upload_deltas_to_s3 duration_ms=%d bucket=%s lines_prefix=%s words_prefix=%s results=%s",
            int((time.time() - t_upload) * 1000),
            chroma_bucket,
            lines_delta_prefix,
            words_delta_prefix,
            results,
        )

        # Create CompactionRun so Dynamo stream enqueues compaction jobs
        t_compaction = time.time()
        compaction_run = CompactionRun(
            run_id=run_id,
            image_id=args.image_id,
            receipt_id=int(args.receipt_id),
            lines_delta_prefix=lines_delta_prefix,
            words_delta_prefix=words_delta_prefix,
        )
        dynamo.add_compaction_run(compaction_run)
        logger.info(
            "phase add_compaction_run duration_ms=%d run_id=%s",
            int((time.time() - t_compaction) * 1000),
            run_id,
        )

    logger.info("done assign_missing_receipt_metadata_modular")

    # print(
    #     json.dumps(
    #         {
    #             **result,
    #             "delta_paths": {
    #                 "lines": delta_lines_db,
    #                 "words": delta_words_db,
    #             },
    #         },
    #         indent=2,
    #     )
    # )


if __name__ == "__main__":
    main()
