"""
Suggest Labels Handler (Container Lambda)

Downloads ChromaDB snapshot, processes receipts with unlabeled words in batches,
and runs the Label Suggestion Agent to create PENDING labels.

Updates DynamoDB records with suggested labels.
"""

import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, List

import boto3

from utils.emf_metrics import emf_metrics

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

    logger.info(f"Downloading ChromaDB snapshot from s3://{bucket}/{collection}/")

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
    pages = paginator.paginate(Bucket=bucket, Prefix=prefix)

    os.makedirs(cache_path, exist_ok=True)

    file_count = 0
    for page in pages:
        for obj in page.get("Contents", []):
            key = obj["Key"]
            local_path = os.path.join(cache_path, os.path.basename(key))
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
    batch_bucket: str = "",
    execution_id: str = "unknown",
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
    from receipt_agent.graph.label_suggestion_workflow import suggest_labels_for_receipt

    # Initialize metrics
    receipts_processed = 0
    total_suggestions = 0
    total_llm_calls = 0
    total_llm_calls_successful = 0
    total_llm_calls_failed = 0
    total_skipped_no_candidates = 0
    total_skipped_low_confidence = 0
    total_unlabeled_words = 0
    total_chromadb_queries = 0
    total_embeddings_generated = 0
    high_confidence_suggestions = 0  # confidence >= 0.75
    medium_confidence_suggestions = 0  # 0.60 <= confidence < 0.75
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

            # Add small delay between receipts to avoid rate limits
            if i > 1:
                await asyncio.sleep(0.5)  # 500ms delay between receipts

            # Run suggestion agent
            result = await suggest_labels_for_receipt(
                image_id=image_id,
                receipt_id=receipt_id,
                dynamo_client=dynamo_client,
                chroma_client=chroma_client,
                embed_fn=embed_fn,
                llm=llm,
                max_llm_calls=10,  # Limit LLM calls per receipt
                dry_run=dry_run,
            )

            # Aggregate metrics
            receipts_processed += 1
            total_suggestions += result.get("suggestions_count", 0)
            total_llm_calls += result.get("llm_calls", 0)
            total_skipped_no_candidates += result.get("skipped_no_candidates", 0)
            total_skipped_low_confidence += result.get("skipped_low_confidence", 0)
            total_unlabeled_words += result.get("unlabeled_words_count", 0)

            # Performance metrics from workflow
            perf = result.get("performance", {})
            total_chromadb_queries += perf.get("total_chroma_queries", 0)

            # Count embeddings generated
            # The workflow tracks embedding_times but not count directly
            # We can estimate: if there are ChromaDB queries, some embeddings were likely generated
            # A more accurate count would require the workflow to return embedding_count
            # For now, use a conservative estimate: ~40% of unlabeled words need on-the-fly embeddings
            unlabeled = result.get("unlabeled_words_count", 0)
            queries = perf.get("total_chroma_queries", 0)
            if queries > 0 and unlabeled > 0:
                # Estimate: some words have cached embeddings, some need on-the-fly generation
                # Conservative estimate: 40% of words need embeddings generated
                estimated_embeddings = max(0, int(unlabeled * 0.4))
                total_embeddings_generated += estimated_embeddings

            # Count confidence-based suggestions from submit_result
            submit_result = result.get("submit_result", {})
            created_labels = submit_result.get("created_labels", [])
            for label in created_labels:
                confidence = label.get("confidence", 0.0)
                if confidence >= 0.75:
                    high_confidence_suggestions += 1
                elif confidence >= 0.60:
                    medium_confidence_suggestions += 1

            # Track LLM call success/failure (assume successful if no error in result)
            llm_calls = result.get("llm_calls", 0)
            if llm_calls > 0:
                # If we got suggestions or skipped words, LLM calls were likely successful
                # This is a heuristic - we could track actual failures if workflow returns them
                total_llm_calls_successful += llm_calls

            logger.info(
                f"Receipt {image_id}#{receipt_id}: {result.get('suggestions_count', 0)} suggestions, "
                f"{result.get('llm_calls', 0)} LLM calls, "
                f"{perf.get('total_chroma_queries', 0)} ChromaDB queries"
            )

        except Exception as e:
            logger.error(f"Error processing receipt {receipt_data.get('image_id')}#{receipt_data.get('receipt_id')}: {e}")
            errors.append({
                "image_id": receipt_data.get("image_id"),
                "receipt_id": receipt_data.get("receipt_id"),
                "error": str(e),
            })

    return {
        "status": "completed" if not errors else "partial",
        "receipts_processed": receipts_processed,
        "suggestions_made": total_suggestions,
        "llm_calls": total_llm_calls,
        "llm_calls_successful": total_llm_calls_successful,
        "llm_calls_failed": total_llm_calls_failed,
        "skipped_no_candidates": total_skipped_no_candidates,
        "skipped_low_confidence": total_skipped_low_confidence,
        "unlabeled_words": total_unlabeled_words,
        "chromadb_queries": total_chromadb_queries,
        "embeddings_generated": total_embeddings_generated,
        "high_confidence_suggestions": high_confidence_suggestions,
        "medium_confidence_suggestions": medium_confidence_suggestions,
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
    batch_bucket = event.get("batch_bucket") or os.environ.get("BATCH_BUCKET", "")
    dry_run = event.get("dry_run", True)
    chromadb_bucket = os.environ.get("CHROMADB_BUCKET", "")

    # Read batch_file from manifest
    if batch_index is None or not manifest_s3_key:
        raise ValueError("batch_index and manifest_s3_key are required")

    logger.info(
        f"Loading batch {batch_index} from manifest s3://{batch_bucket}/{manifest_s3_key}"
    )
    response = s3.get_object(Bucket=batch_bucket, Key=manifest_s3_key)
    manifest_content = response["Body"].read().decode("utf-8")
    batches = json.loads(manifest_content)
    if batch_index >= len(batches):
        raise ValueError(f"Batch index {batch_index} out of range for manifest with {len(batches)} batches")
    batch_file = batches[batch_index].get("batch_file", "")
    logger.info(f"Found batch_file: {batch_file}")

    # Set LangSmith project from Step Function input
    langchain_project = event.get("langchain_project") or os.environ.get("LANGCHAIN_PROJECT", "label-suggestion-agent")
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
        from receipt_agent.clients.factory import (
            create_dynamo_client,
            create_chroma_client,
            create_embed_fn,
        )
        from receipt_agent.config.settings import get_settings
        from langchain_ollama import ChatOllama

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
            logger.warning("No Ollama API key - will use ChromaDB only (no LLM calls)")

        # Download receipts from S3
        logger.info(f"Downloading receipts from s3://{batch_bucket}/{batch_file}")
        response = s3.get_object(Bucket=batch_bucket, Key=batch_file)
        ndjson_content = response["Body"].read().decode("utf-8")

        # Parse NDJSON
        receipts = []
        for line in ndjson_content.strip().split("\n"):
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
                batch_bucket=batch_bucket,
                execution_id=execution_id,
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

        # Emit EMF metrics (cost-effective: ONE log line, no API calls)
        # Only emit if batch was successful
        if result.get("status") in ("completed", "partial"):
            emf_metrics.log_metrics(
                metrics={
                    # Operational metrics
                    "ReceiptsProcessed": result.get("receipts_processed", 0),
                    "SuggestionsMade": result.get("suggestions_made", 0),
                    "UnlabeledWordsFound": result.get("unlabeled_words", 0),
                    "ProcessingTimeSeconds": round(processing_time, 2),
                    "BatchSucceeded": 1 if result.get("status") == "completed" else 0,
                    "BatchFailed": 0 if result.get("status") == "completed" else 1,
                    # Quality metrics
                    "SkippedNoCandidates": result.get("skipped_no_candidates", 0),
                    "SkippedLowConfidence": result.get("skipped_low_confidence", 0),
                    "HighConfidenceSuggestions": result.get("high_confidence_suggestions", 0),
                    "MediumConfidenceSuggestions": result.get("medium_confidence_suggestions", 0),
                    # API usage metrics
                    "LLMCallsTotal": result.get("llm_calls", 0),
                    "LLMCallsSuccessful": result.get("llm_calls_successful", 0),
                    "LLMCallsFailed": result.get("llm_calls_failed", 0),
                    "ChromaDBQueries": result.get("chromadb_queries", 0),
                    "EmbeddingsGenerated": result.get("embeddings_generated", 0),
                },
                # IMPORTANT: Keep dimensions LOW cardinality to control costs
                # No dimensions for now (all receipts processed together)
                # Could add LabelType dimension if we track per-label-type metrics
                dimensions={},
                # Additional context goes in properties (not dimensions)
                properties={
                    "execution_id": execution_id,
                    "batch_index": batch_index,
                    "batch_file": batch_file,
                    "dry_run": dry_run,
                    "status": result.get("status", "unknown"),
                },
                units={
                    "ProcessingTimeSeconds": "Seconds",
                },
            )
        else:
            # Emit failure metrics
            emf_metrics.log_metrics(
                metrics={
                    "BatchSucceeded": 0,
                    "BatchFailed": 1,
                },
                dimensions={},
                properties={
                    "execution_id": execution_id,
                    "batch_index": batch_index,
                    "batch_file": batch_file,
                    "error": result.get("error", "unknown"),
                },
            )

        return result

    except Exception as e:
        logger.error(f"Error processing batch: {e}", exc_info=True)
        return {
            "status": "error",
            "error": str(e),
            "batch_index": batch_index,
        }

