"""
Harmonize Labels V3 Handler (Container Lambda)

Processes receipts using LabelHarmonizerV3 to validate and harmonize all labels
on whole receipts, ensuring financial consistency.

Uses LabelHarmonizerV3 which uses an LLM agent for intelligent reasoning.

Dependencies (via pyproject.toml):
- receipt_agent: LabelHarmonizerV3
- receipt_dynamo: DynamoClient
- receipt_chroma: ChromaClient (optional, for similarity search)

Environment Variables (set by infrastructure.py):
- Lambda-specific:
  - BATCH_BUCKET: S3 bucket for batch files
  - CHROMADB_BUCKET: S3 bucket with ChromaDB snapshots
- receipt_agent Settings (RECEIPT_AGENT_* prefix):
  - RECEIPT_AGENT_DYNAMO_TABLE_NAME: DynamoDB table name
  - RECEIPT_AGENT_OPENAI_API_KEY: OpenAI API key for embeddings
  - RECEIPT_AGENT_OLLAMA_API_KEY: Ollama API key for LLM
  - RECEIPT_AGENT_OLLAMA_BASE_URL: Ollama API base URL
  - RECEIPT_AGENT_OLLAMA_MODEL: Ollama model name
  - RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY: ChromaDB local path
- LangSmith tracing:
  - LANGCHAIN_API_KEY: LangSmith API key
  - LANGCHAIN_TRACING_V2: Enable tracing
  - LANGCHAIN_ENDPOINT: LangSmith endpoint
  - LANGCHAIN_PROJECT: LangSmith project name
"""

import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, List

import boto3
from utils.emf_metrics import emf_metrics

# LangSmith tracing - ensure traces are flushed before Lambda exits
try:
    from langsmith.run_trees import get_cached_client as get_langsmith_client

    HAS_LANGSMITH = True
except ImportError:
    HAS_LANGSMITH = False
    get_langsmith_client = None  # type: ignore


def flush_langsmith_traces():
    """
    Flush all pending LangSmith traces to the API.

    Must be called before Lambda returns to prevent traces from getting
    stuck as "pending" in LangSmith. Lambda freezes/terminates background
    threads after returning, so we must explicitly flush.
    """
    if HAS_LANGSMITH and get_langsmith_client:
        try:
            client = get_langsmith_client()
            client.flush()
            logger.info("LangSmith traces flushed successfully")
        except Exception as e:
            logger.warning(f"Failed to flush LangSmith traces: {e}")


logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Suppress noisy HTTP request logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

s3 = boto3.client("s3")


def download_chromadb_snapshot(
    bucket: str, collection: str, cache_path: str
) -> str:
    """
    Download ChromaDB snapshot from S3 if not cached.

    Uses atomic pointer pattern for consistent reads.
    """
    # Check if already cached
    chroma_db_file = os.path.join(cache_path, "chroma.sqlite3")
    if os.path.exists(chroma_db_file):
        logger.info(f"ChromaDB already cached at {cache_path}")
        return cache_path

    logger.info(
        f"Downloading ChromaDB snapshot from s3://{bucket}/{collection}/"
    )

    # Get latest pointer
    pointer_key = f"{collection}/snapshot/latest-pointer.txt"
    try:
        response = s3.get_object(Bucket=bucket, Key=pointer_key)
        timestamp = response["Body"].read().decode().strip()
        logger.info(f"Latest snapshot timestamp: {timestamp}")
    except Exception as e:
        logger.error(f"Failed to get pointer: {e}")
        raise

    # Download snapshot files
    prefix = f"{collection}/snapshot/timestamped/{timestamp}/"
    paginator = s3.get_paginator("list_objects_v2")

    os.makedirs(cache_path, exist_ok=True)
    downloaded_files = 0

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            relative_path = key[len(prefix) :]
            if not relative_path or key.endswith(".snapshot_hash"):
                continue

            local_path = os.path.join(cache_path, relative_path)
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            s3.download_file(bucket, key, local_path)
            downloaded_files += 1

    logger.info(f"Downloaded {downloaded_files} files to {cache_path}")
    return cache_path


async def process_receipt_batch(
    receipts: List[Dict[str, Any]],
    dynamo_client: Any,
    chroma_client: Any,
    embed_fn: Any,
    llm: Any,
    dry_run: bool = True,
    min_confidence: float = 0.7,
) -> Dict[str, Any]:
    """Process a batch of receipts using LabelHarmonizerV3."""
    from receipt_agent.agents.label_harmonizer.tools import LabelHarmonizerV3

    # Create harmonizer
    harmonizer = LabelHarmonizerV3(
        dynamo_client=dynamo_client,
        chroma_client=chroma_client,
        embed_fn=embed_fn,
        llm=llm,
    )

    # Process receipts
    receipt_keys = [(r["image_id"], r["receipt_id"]) for r in receipts]
    results = await harmonizer.harmonize_receipts(
        receipt_keys=receipt_keys,
        dry_run=dry_run,
    )

    # Apply fixes if not dry run
    update_result = None
    if not dry_run:
        logger.info(
            "Applying fixes (dry_run=False, min_confidence=%s)", min_confidence
        )
        update_result = await harmonizer.apply_fixes(
            results=results,
            dry_run=False,
            min_confidence=min_confidence,
        )
        logger.info(
            "Fixes applied: updated=%d, skipped=%d, failed=%d",
            update_result.total_updated,
            update_result.total_skipped,
            update_result.total_failed,
        )

    # Aggregate results
    total_labels = sum(r.total_labels for r in results)
    total_updated = sum(r.labels_updated for r in results)
    total_failed = sum(1 for r in results if r.errors)
    valid_totals = sum(1 for r in results if r.totals_valid)

    return {
        "receipts_processed": len(receipts),
        "total_labels": total_labels,
        "labels_updated": total_updated,
        "receipts_failed": total_failed,
        "receipts_valid_totals": valid_totals,
        "results": [
            {
                "image_id": r.image_id,
                "receipt_id": r.receipt_id,
                "labels_updated": r.labels_updated,
                "currency": r.currency_detected,
                "totals_valid": r.totals_valid,
                "confidence": r.confidence,
                "errors": r.errors,
            }
            for r in results
        ],
        "updates_applied": update_result is not None,
        "total_updated": update_result.total_updated if update_result else 0,
        "total_skipped": update_result.total_skipped if update_result else 0,
        "total_failed": update_result.total_failed if update_result else 0,
    }


async def handler_async(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Harmonize labels for a batch of receipts.

    Input:
    {
        "receipts": [
            {"image_id": "img1", "receipt_id": 1},
            {"image_id": "img2", "receipt_id": 1},
            ...
        ],
        "execution_id": "abc123",
        "dry_run": true,
        "min_confidence": 0.7
    }
    """
    execution_id = event.get("execution_id", "unknown")
    dry_run = event.get("dry_run", True)
    min_confidence = event.get("min_confidence", 0.7)
    receipts = event.get("receipts", [])

    # Lambda-specific env vars
    batch_bucket = event.get("batch_bucket") or os.environ.get(
        "BATCH_BUCKET", ""
    )
    chromadb_bucket = os.environ.get("CHROMADB_BUCKET", "")

    # Set LangSmith project
    langchain_project = event.get("langchain_project") or os.environ.get(
        "LANGCHAIN_PROJECT", "label-harmonizer-v3"
    )
    os.environ["LANGCHAIN_PROJECT"] = langchain_project
    logger.info(f"Using LangSmith project: {langchain_project}")

    logger.info(
        f"Processing {len(receipts)} receipts "
        f"(execution_id={execution_id}, dry_run={dry_run})"
    )

    # Track processing time
    start_time = time.time()

    try:
        # Setup ChromaDB (cached in /tmp)
        chroma_path = os.environ.get(
            "RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY", "/tmp/chromadb"
        )
        if chromadb_bucket:
            download_chromadb_snapshot(chromadb_bucket, "words", chroma_path)
            os.environ["RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY"] = chroma_path

        # Create clients
        from receipt_agent.clients.factory import (
            create_chroma_client,
            create_dynamo_client,
            create_embed_fn,
        )
        from receipt_agent.config.settings import get_settings

        settings = get_settings()
        logger.info(f"Settings loaded: table={settings.dynamo_table_name}")

        dynamo = create_dynamo_client(settings=settings)
        chroma = (
            create_chroma_client(settings=settings)
            if chromadb_bucket
            else None
        )
        embed_fn = create_embed_fn(settings=settings)

        # Create LLM
        from langchain_ollama import ChatOllama

        ollama_api_key = (
            settings.ollama_api_key.get_secret_value()
            if settings.ollama_api_key
            else None
        )
        if not ollama_api_key:
            raise ValueError("RECEIPT_AGENT_OLLAMA_API_KEY not set")

        llm = ChatOllama(
            model=settings.ollama_model,
            base_url=settings.ollama_base_url,
            client_kwargs={
                "headers": {"Authorization": f"Bearer {ollama_api_key}"},
                "timeout": 120,
            },
            temperature=0.3,
        )
        logger.info(
            f"LLM created: model={settings.ollama_model}, "
            f"base_url={settings.ollama_base_url}"
        )

        # Process batch (already async, so await it)
        result = await process_receipt_batch(
            receipts=receipts,
            dynamo_client=dynamo,
            chroma_client=chroma,
            embed_fn=embed_fn,
            llm=llm,
            dry_run=dry_run,
            min_confidence=min_confidence,
        )

        # Upload results
        if batch_bucket:
            results_key = f"results/{execution_id}/batch_{time.time()}.json"
            s3.put_object(
                Bucket=batch_bucket,
                Key=results_key,
                Body=json.dumps(result, indent=2, default=str).encode("utf-8"),
                ContentType="application/json",
            )
            logger.info(
                f"Results uploaded to s3://{batch_bucket}/{results_key}"
            )

        # Emit EMF metrics
        processing_time = time.time() - start_time
        emf_metrics.log_metrics(
            metrics={
                "ReceiptsProcessed": result["receipts_processed"],
                "LabelsUpdated": result["labels_updated"],
                "ReceiptsFailed": result["receipts_failed"],
                "ProcessingTimeSeconds": round(processing_time, 2),
                "BatchSucceeded": 1,
                "BatchFailed": 0,
            },
            dimensions={},  # No high-cardinality dimensions
            properties={
                "execution_id": execution_id,
                "dry_run": dry_run,
            },
            units={
                "ProcessingTimeSeconds": "Seconds",
            },
        )

        # Flush LangSmith traces
        flush_langsmith_traces()

        return {
            "status": "completed",
            "receipts_processed": result["receipts_processed"],
            "labels_updated": result["labels_updated"],
            "receipts_failed": result["receipts_failed"],
            "updates_applied": result.get("updates_applied", False),
        }

    except Exception as e:
        logger.error(f"Error processing batch: {e}", exc_info=True)

        # Emit failure metrics
        processing_time = time.time() - start_time
        emf_metrics.log_metrics(
            metrics={
                "ReceiptsProcessed": 0,
                "LabelsUpdated": 0,
                "ReceiptsFailed": len(receipts),
                "ProcessingTimeSeconds": round(processing_time, 2),
                "BatchSucceeded": 0,
                "BatchFailed": 1,
            },
            dimensions={},
            properties={
                "execution_id": execution_id,
                "error": str(e),
            },
            units={
                "ProcessingTimeSeconds": "Seconds",
            },
        )

        # Flush LangSmith traces even on error
        flush_langsmith_traces()

        return {
            "status": "error",
            "error": str(e),
            "receipts_processed": 0,
            "labels_updated": 0,
            "receipts_failed": len(receipts),
        }


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Lambda handler wrapper for async function."""
    return asyncio.run(handler_async(event, context))
