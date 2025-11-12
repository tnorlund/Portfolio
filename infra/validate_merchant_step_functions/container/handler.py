import os
import tempfile
import uuid
import time
import logging

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities.compaction_run import CompactionRun
from receipt_label.vector_store import VectorClient
from receipt_label.merchant_resolution.contexts import load_receipt_context
from receipt_label.merchant_resolution.embeddings import upsert_embeddings
from receipt_label.embedding.line.realtime import embed_lines_realtime
from receipt_label.embedding.word.realtime import embed_words_realtime
from receipt_label.utils.chroma_s3_helpers import upload_bundled_delta_to_s3


def _embed_fn_from_openai_texts(texts):
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


def lambda_handler(event, _context):
    """
    Lambda handler for validating a merchant.
    """
    # Configure logging (idempotent)
    log_level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)
    # Ensure logs emit in Lambda (basicConfig may be a no-op on re-invokes)
    logger = logging.getLogger(__name__)
    root = logging.getLogger()
    for lg in (root, logger):
        lg.setLevel(log_level)
        if not lg.handlers:
            h = logging.StreamHandler()
            h.setFormatter(
                logging.Formatter(
                    "%(asctime)s %(levelname)s %(name)s: %(message)s"
                )
            )
            lg.addHandler(h)

    t_start = time.time()
    logger.info(
        "validate handler start",
        extra={
            "event_keys": list(event.keys()),
        },
    )
    image_id = event["image_id"]
    receipt_id = int(event["receipt_id"])

    table = os.environ["DYNAMO_TABLE_NAME"]
    bucket = os.environ["CHROMADB_BUCKET"]
    chroma_endpoint = os.environ.get("CHROMA_HTTP_ENDPOINT")

    logger.info(
        "env configured",
        extra={
            "table": table,
            "bucket": bucket,
            "chroma_http_endpoint": chroma_endpoint or "",
            "log_level": log_level_name,
        },
    )

    dynamo = DynamoClient(table)

    # Get API keys
    google_places_key = os.environ.get("GOOGLE_PLACES_API_KEY")
    ollama_key = os.environ.get("OLLAMA_API_KEY")
    langchain_key = os.environ.get("LANGCHAIN_API_KEY")

    if not ollama_key:
        raise ValueError("OLLAMA_API_KEY is required for LangGraph metadata creation")
    if not langchain_key:
        raise ValueError("LANGCHAIN_API_KEY is required for LangGraph metadata creation")

    # Load receipt context first (needed for embeddings anyway)
    key = (image_id, receipt_id)
    ctx = load_receipt_context(dynamo, key)
    receipt_lines = ctx.get("lines", [])
    receipt_words = ctx.get("words", [])

    # Optional ChromaDB client for fast-path (non-time-sensitive, can use HTTP or S3)
    chroma_line_client = None
    embed_fn = None
    if chroma_endpoint:
        try:
            chroma_line_client = VectorClient.create_chromadb_client(
                mode="read", http_url=chroma_endpoint
            )
            logger.info("Using HTTP ChromaDB endpoint for optional fast-path")
            embed_fn = _embed_fn_from_openai_texts
        except Exception as e:
            logger.warning(f"Failed to create HTTP ChromaDB client: {e}. Proceeding without ChromaDB fast-path.")

    # Create metadata using LangGraph workflow
    # Pass lines and words directly (already loaded) to avoid re-reading from DynamoDB
    t_resolve = time.time()
    import asyncio
    from receipt_label.langchain.metadata_creation import create_receipt_metadata_simple

    logger.info("Creating ReceiptMetadata using LangGraph workflow")
    metadata = asyncio.run(
        create_receipt_metadata_simple(
            client=dynamo,
            image_id=image_id,
            receipt_id=receipt_id,
            google_places_api_key=google_places_key,
            ollama_api_key=ollama_key,
            langsmith_api_key=langchain_key,
            thinking_strength="medium",
            receipt_lines=receipt_lines,  # Pass entities directly to avoid re-reading
            receipt_words=receipt_words,  # Pass entities directly to avoid re-reading
            chroma_line_client=chroma_line_client,  # Optional fast-path
            embed_fn=embed_fn,  # Optional embedding function
        )
    )

    wrote_metadata = metadata is not None
    logger.info(
        "resolved receipt",
        extra={
            "image_id": image_id,
            "receipt_id": receipt_id,
            "wrote_metadata": wrote_metadata,
            "merchant_name": metadata.merchant_name if metadata else None,
            "place_id": metadata.place_id if metadata else None,
            "duration_ms": int((time.time() - t_resolve) * 1000),
        },
    )

    # Prepare delta dirs for upsert
    run_id = str(uuid.uuid4())
    delta_lines_db = os.path.join(tempfile.gettempdir(), f"lines_{run_id}")
    delta_words_db = os.path.join(tempfile.gettempdir(), f"words_{run_id}")

    t_delta_init = time.time()
    write_line = VectorClient.create_chromadb_client(
        persist_directory=delta_lines_db, mode="delta", metadata_only=True
    )
    write_word = VectorClient.create_chromadb_client(
        persist_directory=delta_words_db, mode="delta", metadata_only=True
    )
    logger.info(
        "delta dbs initialized",
        extra={
            "delta_lines_db": delta_lines_db,
            "delta_words_db": delta_words_db,
            "duration_ms": int((time.time() - t_delta_init) * 1000),
        },
    )

    # Use merchant name from metadata if available
    merchant_name = metadata.merchant_name if metadata else None

    t_upsert = time.time()
    upsert_embeddings(
        line_client=write_line,
        word_client=write_word,
        line_embed_fn=embed_lines_realtime,
        word_embed_fn=embed_words_realtime,
        ctx=ctx,
        merchant_name=merchant_name,
    )
    logger.info(
        "embeddings upserted to local delta",
        extra={
            "lines_count": len(ctx.get("lines") or []),
            "words_count": len(ctx.get("words") or []),
            "duration_ms": int((time.time() - t_upsert) * 1000),
        },
    )

    # Upload deltas to S3
    lines_prefix = f"lines/delta/{run_id}/"
    words_prefix = f"words/delta/{run_id}/"
    t_upload = time.time()
    res_lines = upload_bundled_delta_to_s3(
        local_delta_dir=delta_lines_db,
        bucket=bucket,
        delta_prefix=lines_prefix,
        metadata={
            "run_id": run_id,
            "image_id": image_id,
            "receipt_id": str(receipt_id),
            "collection": "lines",
        },
    )
    res_words = upload_bundled_delta_to_s3(
        local_delta_dir=delta_words_db,
        bucket=bucket,
        delta_prefix=words_prefix,
        metadata={
            "run_id": run_id,
            "image_id": image_id,
            "receipt_id": str(receipt_id),
            "collection": "words",
        },
    )
    logger.info(
        "uploaded deltas",
        extra={
            "bucket": bucket,
            "lines_prefix": lines_prefix,
            "words_prefix": words_prefix,
            "run_id": run_id,
            "lines_result": res_lines,
            "words_result": res_words,
            "duration_ms": int((time.time() - t_upload) * 1000),
        },
    )
    compaction_run = CompactionRun(
        run_id=run_id,
        image_id=image_id,
        receipt_id=int(receipt_id),
        lines_delta_prefix=lines_prefix,
        words_delta_prefix=words_prefix,
    )
    logger.info(compaction_run.to_item())
    t_compaction = time.time()
    dynamo.add_compaction_run(compaction_run)
    logger.info(
        "compaction run inserted",
        extra={
            "run_id": run_id,
            "image_id": image_id,
            "receipt_id": receipt_id,
            "lines_delta_prefix": lines_prefix,
            "words_delta_prefix": words_prefix,
            "duration_ms": int((time.time() - t_compaction) * 1000),
        },
    )

    result = {
        "wrote_metadata": wrote_metadata,
        "run_id": run_id,
        "lines_prefix": lines_prefix,
        "words_prefix": words_prefix,
        "uploads": {"lines": res_lines, "words": res_words},
    }
    logger.info(
        "validate handler done",
        extra={
            "total_duration_ms": int((time.time() - t_start) * 1000),
            "result_summary": {
                "wrote_metadata": result["wrote_metadata"],
                "run_id": result["run_id"],
            },
        },
    )
    return result
