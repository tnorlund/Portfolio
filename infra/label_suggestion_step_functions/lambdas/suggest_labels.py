"""
Suggest Labels Handler (Container Lambda)

Downloads ChromaDB snapshot, processes receipts with unlabeled words in batches,
and runs the Label Suggestion Agent to create PENDING labels.

Updates DynamoDB records with suggested labels.
"""

import asyncio
import io
import json
import logging
import os
import time
from typing import Any, Dict, List

import boto3

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
        logger.exception("Failed to get pointer")
        raise

    # Download snapshot files
    prefix = f"{collection}/snapshot/timestamped/{timestamp}/"
    paginator = s3.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=bucket, Prefix=prefix)

    os.makedirs(cache_path, exist_ok=True)

    file_count = 0
    for page in pages:
        for obj in page.get("Contents", []):
            key = obj["Key"]

            # Special handling for chroma.sqlite3: place it directly at cache_path root
            if key.endswith("chroma.sqlite3"):
                local_path = os.path.join(cache_path, "chroma.sqlite3")
            else:
                # Preserve the S3 key's relative path structure for other files
                relative_key = key.lstrip('/')
                local_path = os.path.join(cache_path, relative_key)
                os.makedirs(os.path.dirname(local_path), exist_ok=True)

            s3.download_file(bucket, key, local_path)
            file_count += 1

    logger.info(f"Downloaded {file_count} files to {cache_path}")
    return cache_path


async def process_batch(
    receipts: List[Dict[str, Any]],
    dynamo_client: Any,
    chroma_client: Any,
    embed_fn: Any,
    llm: Any,
    dry_run: bool = True,
) -> Dict[str, Any]:
    """
    Process a batch of receipts using the Label Suggestion Agent.

    Args:
        receipts: List of receipt dictionaries
        dynamo_client: DynamoDB client
        chroma_client: ChromaDB client
        embed_fn: Embedding function
        llm: LLM instance (optional)
        dry_run: If True, don't update DynamoDB

    Returns:
        Dictionary with processing results
    """
    from receipt_agent.agents.label_suggestion import (
        suggest_labels_for_receipt,
    )

    # Get configurable limits from environment
    receipt_delay_seconds = float(os.environ.get("RECEIPT_DELAY_SECONDS", "0.5"))
    max_llm_calls_per_receipt = int(os.environ.get("MAX_LLM_CALLS_PER_RECEIPT", "10"))

    # Initialize metrics
    receipts_processed = 0
    total_suggestions = 0
    total_llm_calls = 0
    total_skipped_no_candidates = 0
    total_skipped_low_confidence = 0
    total_unlabeled_words = 0
    errors: List[Dict] = []

    for i, receipt_data in enumerate(receipts, 1):
        try:
            image_id = receipt_data["image_id"]
            receipt_id = receipt_data["receipt_id"]
            unlabeled_count = receipt_data.get("unlabeled_words_count", 0)

            logger.info(
                f"[{i}/{len(receipts)}] Processing receipt {image_id}#{receipt_id} "
                f"({unlabeled_count} unlabeled words)"
            )

            # Add configurable delay between receipts to avoid rate limits
            if i > 1:
                await asyncio.sleep(receipt_delay_seconds)

            # Run suggestion agent
            result = await suggest_labels_for_receipt(
                image_id=image_id,
                receipt_id=receipt_id,
                dynamo_client=dynamo_client,
                chroma_client=chroma_client,
                embed_fn=embed_fn,
                llm=llm,
                max_llm_calls=max_llm_calls_per_receipt,
                dry_run=dry_run,
            )

            # Aggregate metrics
            receipts_processed += 1
            total_suggestions += result.get("suggestions_count", 0)
            total_llm_calls += result.get("llm_calls", 0)
            total_skipped_no_candidates += result.get(
                "skipped_no_candidates", 0
            )
            total_skipped_low_confidence += result.get(
                "skipped_low_confidence", 0
            )
            total_unlabeled_words += result.get("unlabeled_words_count", 0)

            logger.info(
                f"Receipt {image_id}#{receipt_id}: {result.get('suggestions_count', 0)} suggestions, "
                f"{result.get('llm_calls', 0)} LLM calls"
            )

        except Exception as e:
            logger.exception(
                f"Error processing receipt {receipt_data.get('image_id')}#{receipt_data.get('receipt_id')}"
            )
            errors.append(
                {
                    "image_id": receipt_data.get("image_id"),
                    "receipt_id": receipt_data.get("receipt_id"),
                    "error": str(e),
                }
            )

    return {
        "status": "completed" if not errors else "partial",
        "receipts_processed": receipts_processed,
        "suggestions_made": total_suggestions,
        "llm_calls": total_llm_calls,
        "skipped_no_candidates": total_skipped_no_candidates,
        "skipped_low_confidence": total_skipped_low_confidence,
        "unlabeled_words": total_unlabeled_words,
        "errors": errors,
    }


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda handler for processing a batch of receipts.

    Input:
    {
        "batch_index": 0,
        "manifest_s3_key": "batches/abc123/manifest.json",
        "execution_id": "abc123",
        "batch_bucket": "bucket-name",
        "dry_run": true,
        "langchain_project": "label-suggestion-agent"
    }

    Output:
    {
        "status": "completed",
        "receipts_processed": 10,
        "suggestions_made": 50,
        "llm_calls": 5,
        ...
    }
    """
    execution_id = event.get("execution_id", "unknown")
    batch_index = event.get("batch_index")  # Required: index into manifest
    manifest_s3_key = event.get("manifest_s3_key")  # Required: manifest key
    batch_bucket = event.get("batch_bucket") or os.environ.get(
        "BATCH_BUCKET", ""
    )
    dry_run = event.get("dry_run", True)
    chromadb_bucket = os.environ.get("CHROMADB_BUCKET", "")

    # Read batch_file from manifest
    if batch_index is None or not manifest_s3_key:
        raise ValueError("batch_index and manifest_s3_key are required")
    if not batch_bucket:
        raise ValueError("batch_bucket (or env BATCH_BUCKET) is required")
    if not chromadb_bucket:
        raise ValueError("CHROMADB_BUCKET env var is required")

    logger.info(
        f"Loading batch {batch_index} from manifest s3://{batch_bucket}/{manifest_s3_key}"
    )
    response = s3.get_object(Bucket=batch_bucket, Key=manifest_s3_key)
    manifest_content = response["Body"].read().decode("utf-8")
    batches = json.loads(manifest_content)
    if batch_index >= len(batches):
        raise ValueError(
            f"Batch index {batch_index} out of range for manifest with {len(batches)} batches"
        )
    batch_file = batches[batch_index].get("batch_file", "")
    logger.info(f"Found batch_file: {batch_file}")

    # Set LangSmith project from Step Function input
    langchain_project = event.get("langchain_project") or os.environ.get(
        "LANGCHAIN_PROJECT", "label-suggestion-agent"
    )
    os.environ["LANGCHAIN_PROJECT"] = langchain_project
    logger.info(f"Using LangSmith project: {langchain_project}")

    logger.info(
        f"Processing batch {batch_file} "
        f"(execution_id={execution_id}, dry_run={dry_run})"
    )

    start_time = time.time()

    try:
        # Setup ChromaDB (cached in /tmp)
        chroma_path = os.environ.get(
            "RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY", "/tmp/chromadb_words"
        )
        download_chromadb_snapshot(chromadb_bucket, "words", chroma_path)

        # Update environment for receipt_agent to find ChromaDB
        os.environ["RECEIPT_AGENT_CHROMA_PERSIST_DIRECTORY"] = chroma_path

        # Create clients using the receipt_agent factory
        from langchain_ollama import ChatOllama

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

        # Create LLM if API key is available
        llm = None
        if settings.ollama_api_key:
            llm = ChatOllama(
                model=settings.ollama_model or "gpt-oss:120b-cloud",
                base_url=settings.ollama_base_url or "https://ollama.com",
                api_key=settings.ollama_api_key,
            )
            logger.info("LLM initialized for ambiguous cases")
        else:
            logger.warning(
                "No Ollama API key - will use ChromaDB only (no LLM calls)"
            )

        # Download receipts from S3
        logger.info(
            f"Downloading receipts from s3://{batch_bucket}/{batch_file}"
        )
        response = s3.get_object(Bucket=batch_bucket, Key=batch_file)

        # Parse NDJSON (streaming)
        receipts = []
        for line in io.TextIOWrapper(response["Body"], encoding="utf-8"):
            line = line.strip()
            if line:
                receipts.append(json.loads(line))

        logger.info(f"Loaded {len(receipts)} receipts from S3")

        # Process batch
        result = asyncio.run(
            process_batch(
                receipts=receipts,
                dynamo_client=dynamo,
                chroma_client=chroma,
                embed_fn=embed_fn,
                llm=llm,
                dry_run=dry_run,
            )
        )

        # Upload results
        results_key = f"results/{execution_id}/{os.path.basename(batch_file).replace('.ndjson', '.json')}"

        s3.put_object(
            Bucket=batch_bucket,
            Key=results_key,
            Body=json.dumps(result, indent=2, default=str).encode("utf-8"),
            ContentType="application/json",
        )

        logger.info(
            f"Results uploaded to s3://{batch_bucket}/{results_key}: "
            f"{result['suggestions_made']} suggestions made, "
            f"{result['llm_calls']} LLM calls"
        )

        processing_time = time.time() - start_time
        result["processing_time_seconds"] = round(processing_time, 2)
        result["results_path"] = results_key

        return result

    except Exception as e:
        logger.error(f"Error processing batch: {e}", exc_info=True)
        return {
            "status": "error",
            "error": str(e),
            "batch_index": batch_index,
        }
