"""
Harmonize Labels Handler (Container Lambda)

Downloads ChromaDB snapshot, processes merchant group labels,
and identifies semantic outliers using the v3 label harmonizer agent.

Dependencies (via pyproject.toml):
- receipt_agent: LabelHarmonizerV3, create_all_clients
- receipt_dynamo: DynamoClient
- receipt_chroma: ChromaClient

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
"""

import asyncio
import json
import logging
import os
import time
from typing import Any, Dict

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

# Suppress noisy HTTP request logs from httpx/httpcore (used by langchain-ollama)
# These log every "HTTP Request: POST https://ollama.com/api/chat" call
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


async def process_merchant_group(
    labels: list,
    merchant_name: str,
    label_type: str,
    chroma_client: Any,
    dynamo_client: Any,
    embed_fn: Any,
    llm: Any,
    dry_run: bool = True,
    min_confidence: float = 70.0,
) -> Dict[str, Any]:
    """Process a single merchant group using LabelHarmonizerV3."""
    # Import here to avoid cold start overhead if not needed
    from receipt_agent.agents.label_harmonizer.tools.label_harmonizer_v3 import (
        LabelHarmonizerV3,
    )

    # Kept for backwards compatibility with caller signature
    _ = min_confidence

    harmonizer = LabelHarmonizerV3(
        dynamo_client=dynamo_client,
        chroma_client=chroma_client,
        embed_fn=embed_fn,
        llm=llm,
    )

    # Harmonize all unique receipts in the batch
    receipt_keys = sorted(
        {
            (l["image_id"], l["receipt_id"])
            for l in labels
            if "image_id" in l and "receipt_id" in l
        }
    )

    logger.info(
        "Analyzing %s labels across %s receipts for %s (%s)",
        len(labels),
        len(receipt_keys),
        merchant_name,
        label_type,
    )

    results = await harmonizer.harmonize_receipts(
        receipt_keys, dry_run=dry_run
    )

    labels_processed = sum(r.total_labels for r in results)
    outliers_found = sum(r.labels_failed for r in results)
    total_updated = sum(len(r.updates) for r in results)
    total_skipped = sum(r.labels_skipped for r in results)
    total_failed = sum(r.labels_failed for r in results)
    total_needs_review = sum(1 for r in results if r.errors)

    outlier_details = [
        {
            "image_id": r.image_id,
            "receipt_id": r.receipt_id,
            "confidence": r.confidence,
            "reasoning": r.reasoning,
            "errors": r.errors,
            "updates": r.updates,
        }
        for r in results
        if r.labels_failed or r.errors
    ]

    summary = {
        "merchant_name": merchant_name,
        "label_type": label_type,
        "labels_processed": labels_processed,
        "outliers_found": outliers_found,
        "consensus": None,
        "outlier_details": outlier_details,
        "results": [r.__dict__ for r in results],
        "dry_run": dry_run,
    }

    if not dry_run:
        summary.update(
            {
                "updates_applied": total_updated > 0,
                "total_updated": total_updated,
                "total_skipped": total_skipped,
                "total_failed": total_failed,
                "total_needs_review": total_needs_review,
            }
        )
    else:
        summary["updates_applied"] = False

    return summary


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Harmonize labels for a single merchant group.

    1. Download ChromaDB snapshot (cached in /tmp)
    2. Stream labels from S3 NDJSON file
    3. Run harmonization with LLM-based outlier detection
    4. Upload results to S3

    Input (two formats supported):
    1. Direct merchant_group object (legacy):
    {
        "merchant_group": {
            "merchant_name": "Sprouts",
            "batch_file": "batches/.../sprouts.ndjson",
            "label_type": "GRAND_TOTAL"
        },
        "execution_id": "abc123",
        "dry_run": true
    }

    2. Index-based (best practice for large payloads):
    {
        "index": 0,
        "work_items_manifest_s3_key": "batches/.../combined_manifest.json",
        "execution_id": "abc123",
        "batch_bucket": "bucket-name",
        "dry_run": true,
        "min_confidence": 75.0  // Optional, default 70.0
    }
    """
    execution_id = event.get("execution_id", "unknown")
    dry_run = event.get("dry_run", True)
    min_confidence = event.get(
        "min_confidence", 70.0
    )  # Minimum confidence for apply_fixes

    # Lambda-specific env vars
    batch_bucket = event.get("batch_bucket") or os.environ.get(
        "BATCH_BUCKET", ""
    )
    chromadb_bucket = os.environ.get("CHROMADB_BUCKET", "")

    # Set LangSmith project from Step Function input (defaults to env var if not provided)
    langchain_project = event.get("langchain_project") or os.environ.get(
        "LANGCHAIN_PROJECT", "label-harmonizer"
    )
    os.environ["LANGCHAIN_PROJECT"] = langchain_project
    logger.info(f"Using LangSmith project: {langchain_project}")

    # Support both direct merchant_group and index-based patterns
    if "index" in event and "work_items_manifest_s3_key" in event:
        # Index-based: read work item from S3 manifest
        index = event["index"]
        manifest_key = event["work_items_manifest_s3_key"]

        logger.info(
            "Loading work item %d from manifest s3://%s/%s",
            index,
            batch_bucket,
            manifest_key,
        )

        response = s3.get_object(Bucket=batch_bucket, Key=manifest_key)
        manifest_content = response["Body"].read().decode("utf-8")
        work_items = json.loads(manifest_content)

        if index >= len(work_items):
            raise ValueError(
                f"Index {index} out of range for manifest with {len(work_items)} items"
            )

        merchant_group = work_items[index]
    else:
        # Legacy: direct merchant_group object
        merchant_group = event["merchant_group"]

    merchant_name = merchant_group["merchant_name"]
    label_type = merchant_group["label_type"]
    batch_file = merchant_group["batch_file"]

    logger.info(
        f"Processing {merchant_name} - {label_type} "
        f"(execution_id={execution_id}, dry_run={dry_run})"
    )

    # Track processing time for metrics
    start_time = time.time()

    try:
        # Setup ChromaDB (cached in /tmp)
        chroma_path = os.environ.get(
            "RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY", "/tmp/chromadb"
        )
        download_chromadb_snapshot(chromadb_bucket, "words", chroma_path)

        # Update environment for receipt_agent to find ChromaDB
        # The factory uses RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY
        os.environ["RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY"] = chroma_path

        # Create clients using the receipt_agent factory
        # This ensures all environment variables are properly used
        from receipt_agent.clients.factory import (
            create_chroma_client,
            create_dynamo_client,
            create_embed_fn,
        )
        from receipt_agent.config.settings import get_settings

        settings = get_settings()
        logger.info(f"Settings loaded: table={settings.dynamo_table_name}")

        dynamo = create_dynamo_client(settings=settings)
        chroma = create_chroma_client(settings=settings)
        embed_fn = create_embed_fn(settings=settings)

        # Create LLM using settings (matching CLI pattern)
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
                "timeout": 120,  # Longer timeout for agent reasoning
            },
            temperature=0.3,
        )
        logger.info(
            f"LLM created: model={settings.ollama_model}, "
            f"base_url={settings.ollama_base_url}"
        )

        # Download labels from S3
        logger.info(
            f"Downloading labels from s3://{batch_bucket}/{batch_file}"
        )
        response = s3.get_object(Bucket=batch_bucket, Key=batch_file)
        ndjson_content = response["Body"].read().decode("utf-8")

        # Parse NDJSON
        labels = []
        for line in ndjson_content.strip().split("\n"):
            if line:
                labels.append(json.loads(line))

        logger.info(f"Loaded {len(labels)} labels from S3")

        # Run harmonization
        result = asyncio.run(
            process_merchant_group(
                labels=labels,
                merchant_name=merchant_name,
                label_type=label_type,
                chroma_client=chroma,
                dynamo_client=dynamo,
                embed_fn=embed_fn,
                llm=llm,
                dry_run=dry_run,
                min_confidence=min_confidence,
            )
        )

        # Upload results
        safe_name = (
            merchant_name.lower()
            .replace(" ", "-")
            .replace("/", "-")
            .replace("'", "")
        )
        results_key = f"results/{execution_id}/{label_type}/{safe_name}.json"

        s3.put_object(
            Bucket=batch_bucket,
            Key=results_key,
            Body=json.dumps(result, indent=2, default=str).encode("utf-8"),
            ContentType="application/json",
        )

        logger.info(
            f"Results uploaded to s3://{batch_bucket}/{results_key}: "
            f"{result['outliers_found']} outliers found"
        )

        # Emit EMF metrics (cost-effective: ONE log line, no API calls)
        processing_time = time.time() - start_time
        emf_metrics.log_metrics(
            metrics={
                "OutliersDetected": result["outliers_found"],
                "LabelsProcessed": result["labels_processed"],
                "ProcessingTimeSeconds": round(processing_time, 2),
                "BatchSucceeded": 1,
                "BatchFailed": 0,
            },
            # IMPORTANT: Keep dimensions LOW cardinality to control costs
            # LabelType has ~15 values (CORE_LABELS)
            # Do NOT add Merchant as dimension (1000s of values = $$$)
            dimensions={"LabelType": label_type},
            # Additional context goes in properties (not dimensions)
            properties={
                "merchant_name": merchant_name,
                "execution_id": execution_id,
                "dry_run": dry_run,
                "batch_file": batch_file,
            },
            units={
                "ProcessingTimeSeconds": "Seconds",
            },
        )

        # CRITICAL: Flush LangSmith traces before returning
        # Lambda freezes background threads after return, causing traces to hang
        flush_langsmith_traces()

        # Return minimal payload to stay under Step Functions 256KB limit
        # Full results are saved to S3 at results_path
        return {
            "status": "completed",
            "results_path": f"s3://{batch_bucket}/{results_key}",
            # Summary only (not full outlier_details or results)
            "merchant_name": result["merchant_name"],
            "label_type": result["label_type"],
            "labels_processed": result["labels_processed"],
            "outliers_found": result["outliers_found"],
            "consensus": result["consensus"],
            # DynamoDB update stats (only populated when dry_run=False)
            "updates_applied": result.get("updates_applied", False),
            "total_updated": result.get("total_updated", 0),
            "total_skipped": result.get("total_skipped", 0),
            "total_failed": result.get("total_failed", 0),
            "total_needs_review": result.get("total_needs_review", 0),
        }

    except Exception as e:
        logger.error(f"Error processing {merchant_name}: {e}", exc_info=True)

        # Emit failure metrics
        processing_time = time.time() - start_time
        emf_metrics.log_metrics(
            metrics={
                "OutliersDetected": 0,
                "LabelsProcessed": 0,
                "ProcessingTimeSeconds": round(processing_time, 2),
                "BatchSucceeded": 0,
                "BatchFailed": 1,
            },
            dimensions={"LabelType": label_type},
            properties={
                "merchant_name": merchant_name,
                "execution_id": execution_id,
                "error": str(e),
            },
            units={
                "ProcessingTimeSeconds": "Seconds",
            },
        )

        # CRITICAL: Flush LangSmith traces even on error
        flush_langsmith_traces()

        return {
            "status": "error",
            "error": str(e),
            "merchant_name": merchant_name,
            "label_type": label_type,
            # Include these for ResultSelector consistency
            "results_path": None,
            "outliers_found": 0,
            "updates_applied": False,
            "total_updated": 0,
            "total_skipped": 0,
            "total_failed": 0,
            "total_needs_review": 0,
        }
