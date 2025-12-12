"""
Validate Labels Handler (Container Lambda)

Downloads ChromaDB snapshot, processes NEEDS_REVIEW labels in batches,
and runs the Label Validation Agent to make VALID/INVALID decisions.

Updates DynamoDB records based on agent decisions.
"""

import asyncio
import json
import logging
import os
import time
from collections import Counter
from typing import Any, Dict, List, Optional

import boto3
from utils.emf_metrics import emf_metrics

from receipt_dynamo.constants import ValidationStatus

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Suppress noisy HTTP request logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

s3 = boto3.client("s3")


class OllamaRateLimitError(RuntimeError):
    """Custom exception for Ollama rate limit errors.

    Step Functions can catch this by error type name.
    """

    pass


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


def _get_label_id(label_data: Dict[str, Any]) -> str:
    """Generate unique ID for a label."""
    return f"{label_data.get('image_id')}#{label_data.get('receipt_id')}#{label_data.get('line_id')}#{label_data.get('word_id')}#{label_data.get('label')}"


def _save_state_to_s3(
    batch_bucket: str,
    execution_id: str,
    batch_file: str,
    processed_label_ids: set,
    results: List[Dict[str, Any]],
    remaining_labels: List[Dict[str, Any]],
    metrics: Dict[str, Any],
) -> str:
    """Save processing state to S3 for circuit breaker recovery."""
    state_key = f"state/{execution_id}/{os.path.basename(batch_file).replace('.ndjson', '.state.json')}"

    state = {
        "processed_label_ids": list(processed_label_ids),
        "processed_results": results,
        "remaining_labels": remaining_labels,
        "metrics": metrics,
        "timestamp": time.time(),
    }

    try:
        s3.put_object(
            Bucket=batch_bucket,
            Key=state_key,
            Body=json.dumps(state, indent=2, default=str).encode("utf-8"),
            ContentType="application/json",
        )
        logger.info(f"State saved to s3://{batch_bucket}/{state_key}")
        return state_key
    except Exception as e:
        logger.exception("Failed to save state to S3")
        raise


def _load_state_from_s3(
    batch_bucket: str, execution_id: str, batch_file: str
) -> Optional[Dict[str, Any]]:
    """Load processing state from S3 if it exists."""
    state_key = f"state/{execution_id}/{os.path.basename(batch_file).replace('.ndjson', '.state.json')}"

    try:
        response = s3.get_object(Bucket=batch_bucket, Key=state_key)
        state_content = response["Body"].read().decode("utf-8")
        state = json.loads(state_content)
        logger.info(f"Loaded state from s3://{batch_bucket}/{state_key}")
        return state
    except s3.exceptions.NoSuchKey:
        logger.info(
            f"No existing state found at s3://{batch_bucket}/{state_key}"
        )
        return None
    except Exception as e:
        logger.warning(f"Failed to load state from S3: {e}")
        return None


def _delete_state_from_s3(
    batch_bucket: str, execution_id: str, batch_file: str
) -> None:
    """Delete state file from S3 after successful completion."""
    state_key = f"state/{execution_id}/{os.path.basename(batch_file).replace('.ndjson', '.state.json')}"

    try:
        s3.delete_object(Bucket=batch_bucket, Key=state_key)
        logger.info(f"Deleted state file s3://{batch_bucket}/{state_key}")
    except Exception as e:
        logger.warning(f"Failed to delete state file: {e}")


async def process_batch(
    labels: List[Dict[str, Any]],
    dynamo_client: Any,
    graph: Any,
    state_holder: Dict[str, Any],
    dry_run: bool = True,
    min_confidence: float = 0.8,
    batch_bucket: str = "",
    execution_id: str = "unknown",
    batch_file: str = "",
) -> Dict[str, Any]:
    """
    Process a batch of labels using the Label Validation Agent.

    Args:
        labels: List of label dictionaries
        dynamo_client: DynamoDB client
        graph: Compiled LangGraph workflow
        state_holder: State holder for the graph
        dry_run: If True, don't update DynamoDB
        min_confidence: Minimum confidence to apply updates (default: 0.8)

    Returns:
        Dictionary with processing results
    """
    from receipt_agent.agents.label_validation import run_label_validation

    # Check for existing state (circuit breaker recovery)
    existing_state = None
    if batch_bucket and execution_id and batch_file:
        existing_state = _load_state_from_s3(
            batch_bucket, execution_id, batch_file
        )

    # Initialize metrics
    results = []
    valid_count = 0
    invalid_count = 0
    needs_review_count = 0
    updated_count = 0
    skipped_count = 0
    failed_count = 0

    # API usage metrics (like harmonizer)
    llm_calls_total = 0
    llm_calls_successful = 0
    llm_calls_failed = 0
    server_errors = 0  # 5xx errors
    timeout_errors = 0

    # Circuit breaker: stop processing if we hit too many rate limits
    circuit_breaker_threshold = 5  # Stop after 5 consecutive rate limit errors
    rate_limit_errors = 0
    circuit_breaker_triggered = False

    # If we have existing state, restore it and filter labels
    processed_label_ids = set()
    if existing_state:
        logger.info(
            f"Resuming from saved state: {len(existing_state.get('processed_label_ids', []))} labels already processed"
        )
        processed_label_ids = set(
            existing_state.get("processed_label_ids", [])
        )
        results = existing_state.get("processed_results", [])

        # Restore metrics from state
        restored_metrics = existing_state.get("metrics", {})
        valid_count = restored_metrics.get("valid_count", 0)
        invalid_count = restored_metrics.get("invalid_count", 0)
        needs_review_count = restored_metrics.get("needs_review_count", 0)
        updated_count = restored_metrics.get("updated_count", 0)
        skipped_count = restored_metrics.get("skipped_count", 0)
        failed_count = restored_metrics.get("failed_count", 0)
        llm_calls_total = restored_metrics.get("llm_calls_total", 0)
        llm_calls_successful = restored_metrics.get("llm_calls_successful", 0)
        llm_calls_failed = restored_metrics.get("llm_calls_failed", 0)
        server_errors = restored_metrics.get("server_errors", 0)
        timeout_errors = restored_metrics.get("timeout_errors", 0)

        # Filter out already-processed labels
        labels = [
            label
            for label in labels
            if _get_label_id(label) not in processed_label_ids
        ]
        logger.info(f"Filtered to {len(labels)} remaining labels to process")

    for i, label_data in enumerate(labels, 1):
        # Check circuit breaker before processing
        if circuit_breaker_triggered:
            logger.warning(
                f"Circuit breaker triggered: stopping label processing after {rate_limit_errors} "
                f"consecutive rate limit errors. Remaining labels: {len(labels) - i + 1}"
            )
            # Mark remaining labels as failed
            for remaining_label in labels[i - 1 :]:
                failed_count += 1
                results.append(
                    {
                        "image_id": remaining_label.get("image_id"),
                        "receipt_id": remaining_label.get("receipt_id"),
                        "line_id": remaining_label.get("line_id"),
                        "word_id": remaining_label.get("word_id"),
                        "label": remaining_label.get("label"),
                        "word_text": remaining_label.get("word_text", ""),
                        "error": "Circuit breaker triggered: too many rate limit errors",
                    }
                )
            break

        try:
            logger.info(
                f"[{i}/{len(labels)}] Validating: '{label_data.get('word_text', '')}' -> "
                f"{label_data.get('label', '')}"
            )

            # Add configurable delay between labels to avoid rate limits
            # Each label makes multiple LLM calls (agent + tools), so spacing helps
            if i > 1:
                # Get delay from environment or use default
                delay_seconds = float(os.environ.get("LABEL_DELAY_SECONDS", "0.5"))
                await asyncio.sleep(delay_seconds)

            # Run validation agent
            # Track LLM calls: Each agent run makes at least 1 LLM call (agent_node)
            # Additional calls happen for tool invocations and follow-up agent calls
            try:
                result = await run_label_validation(
                    graph=graph,
                    state_holder=state_holder,
                    word_text=label_data.get("word_text", ""),
                    suggested_label_type=label_data.get("label", ""),
                    merchant_name=label_data.get("merchant_name"),
                    original_reasoning=label_data.get("reasoning", ""),
                    image_id=label_data.get("image_id"),
                    receipt_id=label_data.get("receipt_id"),
                    line_id=label_data.get("line_id"),
                    word_id=label_data.get("word_id"),
                )

                # Count LLM calls from agent workflow
                # Each tool call + agent response = additional LLM call
                tools_used = result.get("tools_used", [])
                # Estimate: 1 initial call + 1 per tool call (agent processes tool results)
                estimated_llm_calls = 1 + len(tools_used)
                llm_calls_total += estimated_llm_calls
                llm_calls_successful += estimated_llm_calls

            except Exception:
                # Track failed LLM call (at least 1 call was attempted)
                llm_calls_total += 1

                # Re-raise to be handled by outer exception handler
                raise

            decision = result.get("decision", "NEEDS_REVIEW")
            confidence = result.get("confidence", 0.0)

            # Reset rate limit error counter on success
            rate_limit_errors = 0

            # Count decisions
            if decision == "VALID":
                valid_count += 1
            elif decision == "INVALID":
                invalid_count += 1
            else:
                needs_review_count += 1

            # Update DynamoDB if not dry_run and confidence is high enough
            if not dry_run and confidence >= min_confidence:
                try:
                    # Get the label
                    label = dynamo_client.get_receipt_word_label(
                        image_id=label_data.get("image_id"),
                        receipt_id=label_data.get("receipt_id"),
                        line_id=label_data.get("line_id"),
                        word_id=label_data.get("word_id"),
                        label=label_data.get("label"),
                    )

                    if label:
                        # Update validation_status
                        if decision == "VALID":
                            label.validation_status = (
                                ValidationStatus.VALID.value
                            )
                            updated_count += 1
                        elif decision == "INVALID":
                            label.validation_status = (
                                ValidationStatus.INVALID.value
                            )
                            updated_count += 1
                        # NEEDS_REVIEW: leave as is

                        # Update reasoning
                        if result.get("reasoning"):
                            label.reasoning = result.get("reasoning")

                        # Save to DynamoDB
                        dynamo_client.update_receipt_word_label(label)
                        logger.info(
                            f"Updated label: {decision} ({confidence:.0%} confidence)"
                        )
                    else:
                        logger.warning(
                            "Label not found in DynamoDB, skipping update"
                        )
                        skipped_count += 1
                except Exception as e:
                    logger.error(f"Failed to update label in DynamoDB: {e}")
                    failed_count += 1
            else:
                if dry_run:
                    logger.info(
                        f"DRY RUN: Would update to {decision} ({confidence:.0%})"
                    )
                else:
                    logger.info(
                        f"Skipped update: confidence {confidence:.0%} < {min_confidence:.0%}"
                    )
                skipped_count += 1

            # Track this label as processed
            label_id = _get_label_id(label_data)
            processed_label_ids.add(label_id)

            results.append(
                {
                    "image_id": label_data.get("image_id"),
                    "receipt_id": label_data.get("receipt_id"),
                    "line_id": label_data.get("line_id"),
                    "word_id": label_data.get("word_id"),
                    "label": label_data.get("label"),
                    "word_text": label_data.get("word_text", ""),
                    "decision": decision,
                    "confidence": confidence,
                    "reasoning": result.get("reasoning", ""),
                    "evidence": result.get("evidence", []),
                    "tools_used": result.get("tools_used", []),
                }
            )

        except OllamaRateLimitError as e:
            # Rate limit error - track and check circuit breaker
            rate_limit_errors += 1
            llm_calls_failed += 1  # Track as failed LLM call
            logger.warning(
                f"Rate limit error {rate_limit_errors}/{circuit_breaker_threshold} for label {i}: {e}"
            )

            if rate_limit_errors >= circuit_breaker_threshold:
                circuit_breaker_triggered = True
                logger.error(
                    f"Circuit breaker triggered: {rate_limit_errors} consecutive rate limit errors. "
                    f"Stopping label processing to prevent API spam. "
                    f"This Lambda will fail and Step Function will retry the batch later."
                )

                # Save state to S3 before failing (if we have bucket info)
                if batch_bucket and execution_id and batch_file:
                    remaining_labels = labels[
                        i - 1 :
                    ]  # Labels not yet processed
                    metrics = {
                        "valid_count": valid_count,
                        "invalid_count": invalid_count,
                        "needs_review_count": needs_review_count,
                        "updated_count": updated_count,
                        "skipped_count": skipped_count,
                        "failed_count": failed_count,
                        "llm_calls_total": llm_calls_total,
                        "llm_calls_successful": llm_calls_successful,
                        "llm_calls_failed": llm_calls_failed,
                        "server_errors": server_errors,
                        "timeout_errors": timeout_errors,
                    }
                    try:
                        _save_state_to_s3(
                            batch_bucket=batch_bucket,
                            execution_id=execution_id,
                            batch_file=batch_file,
                            processed_label_ids=processed_label_ids,
                            results=results,
                            remaining_labels=remaining_labels,
                            metrics=metrics,
                        )
                        logger.info(
                            "State saved to S3 for circuit breaker recovery"
                        )
                    except Exception as save_error:
                        logger.exception(
                            f"Failed to save state before circuit breaker: {save_error}"
                        )

                # Re-raise to fail the Lambda (Step Function will retry)
                raise OllamaRateLimitError(
                    f"Circuit breaker triggered: {rate_limit_errors} consecutive rate limit errors. "
                    f"Stopping Lambda to prevent API spam."
                ) from e

            # If not at threshold yet, re-raise for Step Function retry
            raise

        except Exception as e:
            error_str = str(e)
            logger.error(f"Error processing label {i}: {e}", exc_info=True)

            # Track LLM call failure
            llm_calls_failed += 1

            # Check if this is a rate limit error (non-OllamaRateLimitError exception)
            is_rate_limit = (
                "429" in error_str
                or "rate limit" in error_str.lower()
                or "rate_limit" in error_str.lower()
                or "too many concurrent requests" in error_str.lower()
                or "too many requests" in error_str.lower()
            )

            if is_rate_limit:
                rate_limit_errors += 1
                logger.warning(
                    f"Rate limit error {rate_limit_errors}/{circuit_breaker_threshold} for label {i}: {e}"
                )
            # Track other error types
            elif (
                "500" in error_str
                or "502" in error_str
                or "503" in error_str
                or "504" in error_str
            ):
                server_errors += 1
            elif (
                "timeout" in error_str.lower()
                or "timed out" in error_str.lower()
            ):
                timeout_errors += 1
                # Re-raise timeout errors for Step Function retry
                raise

            # Check circuit breaker threshold after incrementing rate_limit_errors
            if rate_limit_errors >= circuit_breaker_threshold:
                circuit_breaker_triggered = True
                logger.error(
                    f"Circuit breaker triggered: {rate_limit_errors} consecutive rate limit errors. "
                    f"Stopping label processing to prevent API spam."
                )
                # Re-raise as OllamaRateLimitError for Step Function retry
                raise OllamaRateLimitError(
                    f"Circuit breaker triggered: {rate_limit_errors} consecutive rate limit errors. "
                    f"Stopping Lambda to prevent API spam."
                ) from e

            # For rate limit errors that don't trigger the circuit breaker
            if is_rate_limit:
                raise OllamaRateLimitError(
                    f"Rate limit error: {error_str}"
                ) from e

            # Non-rate-limit error - just track as failed
            failed_count += 1
            results.append(
                {
                    "image_id": label_data.get("image_id"),
                    "receipt_id": label_data.get("receipt_id"),
                    "line_id": label_data.get("line_id"),
                    "word_id": label_data.get("word_id"),
                    "label": label_data.get("label"),
                    "word_text": label_data.get("word_text", ""),
                    "error": str(e),
                }
            )

    # If circuit breaker was triggered, fail the Lambda
    if circuit_breaker_triggered:
        # State should already be saved in the exception handler above
        raise OllamaRateLimitError(
            f"Circuit breaker triggered: {rate_limit_errors} consecutive rate limit errors. "
            f"Stopping Lambda to prevent API spam. Step Function will retry this batch later."
        )

    return {
        "labels_processed": len(labels),
        "valid_count": valid_count,
        "invalid_count": invalid_count,
        "needs_review_count": needs_review_count,
        "updated_count": updated_count,
        "skipped_count": skipped_count,
        "failed_count": failed_count,
        "results": results,
        # Circuit breaker tracking
        "circuit_breaker_triggered": circuit_breaker_triggered,
        "rate_limit_errors": rate_limit_errors,
        # API usage metrics (like harmonizer)
        "api_metrics": {
            "llm_calls_total": llm_calls_total,
            "llm_calls_successful": llm_calls_successful,
            "llm_calls_failed": llm_calls_failed,
            "rate_limit_errors": rate_limit_errors,
            "server_errors": server_errors,
            # Step Function handles retries, not Lambda
            "circuit_breaker_triggers": 1 if circuit_breaker_triggered else 0,
            "timeout_errors": timeout_errors,
        },
    }


def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Validate NEEDS_REVIEW labels using the Label Validation Agent.

    1. Download ChromaDB snapshot (cached in /tmp)
    2. Load labels from S3 batch file
    3. Run validation agent on each label
    4. Update DynamoDB based on decisions (if not dry_run)
    5. Upload results to S3

    Input:
    {
        "batch_file": "batches/abc123/batch1.ndjson",
        "execution_id": "abc123",
        "batch_bucket": "bucket-name",
        "dry_run": true,
        "min_confidence": 0.8  // Optional, default 0.8
    }

    Output:
    {
        "status": "completed",
        "results_path": "s3://bucket/results/...",
        "labels_processed": 50,
        "valid_count": 30,
        "invalid_count": 15,
        "needs_review_count": 5,
        "updated_count": 45,
        "skipped_count": 0,
        "failed_count": 5
    }
    """
    execution_id = event.get("execution_id", "unknown")
    batch_file = event.get("batch_file", "")
    batch_index = event.get("batch_index")  # Optional: index into manifest
    manifest_s3_key = event.get("manifest_s3_key")  # Optional: manifest key
    batch_bucket = event.get("batch_bucket") or os.environ.get(
        "BATCH_BUCKET", ""
    )
    dry_run = event.get("dry_run", True)
    min_confidence = event.get("min_confidence", 0.8)
    chromadb_bucket = os.environ.get("CHROMADB_BUCKET", "")

    # If batch_index and manifest_s3_key are provided, read batch_file from manifest
    if batch_index is not None and manifest_s3_key:
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
        "LANGCHAIN_PROJECT", "label-validation-agent"
    )
    os.environ["LANGCHAIN_PROJECT"] = langchain_project
    logger.info(f"Using LangSmith project: {langchain_project}")

    logger.info(
        f"Processing batch {batch_file} "
        f"(execution_id={execution_id}, dry_run={dry_run}, min_confidence={min_confidence})"
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
        from receipt_agent.agents.label_validation import (
            create_label_validation_graph,
        )
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

        # Create the validation graph
        graph, state_holder = create_label_validation_graph(
            dynamo_client=dynamo,
            chroma_client=chroma,
            embed_fn=embed_fn,
            settings=settings,
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

        # Process batch
        result = asyncio.run(
            process_batch(
                labels=labels,
                dynamo_client=dynamo,
                graph=graph,
                state_holder=state_holder,
                dry_run=dry_run,
                min_confidence=min_confidence,
                batch_bucket=batch_bucket,
                execution_id=execution_id,
                batch_file=batch_file,
            )
        )

        # If successful, delete state file (if it exists)
        if not result.get("circuit_breaker_triggered", False):
            _delete_state_from_s3(batch_bucket, execution_id, batch_file)

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
            f"{result['valid_count']} VALID, {result['invalid_count']} INVALID, "
            f"{result['needs_review_count']} NEEDS_REVIEW"
        )

        # Emit EMF metrics (cost-effective: ONE log line, no API calls)
        processing_time = time.time() - start_time

        # Check if circuit breaker was triggered (from result)
        circuit_breaker_triggered = result.get(
            "circuit_breaker_triggered", False
        )
        rate_limit_errors = result.get("rate_limit_errors", 0)
        api_metrics = result.get("api_metrics", {})

        # Get merchant name from first label (if available) for properties
        merchant_name = labels[0].get("merchant_name") if labels else None

        # Aggregate by label type for dimensions (keep cardinality LOW)
        # Count labels by type for dimension aggregation
        label_type_counter = Counter(
            label.get("label", "UNKNOWN") for label in labels
        )
        label_type_counts = dict(label_type_counter)

        # Emit metrics per label type (if multiple types in batch, emit separate metrics)
        # For simplicity, use the most common label type as dimension
        # If all labels are same type, use that; otherwise use "MIXED"
        if len(label_type_counts) == 1:
            primary_label_type = next(iter(label_type_counts.keys()))
        else:
            # Use most common label type as primary dimension
            primary_label_type = (
                label_type_counter.most_common(1)[0][0]
                if label_type_counter
                else "UNKNOWN"
            )

        emf_metrics.log_metrics(
            metrics={
                "ValidCount": result["valid_count"],
                "InvalidCount": result["invalid_count"],
                "NeedsReviewCount": result["needs_review_count"],
                "LabelsProcessed": result["labels_processed"],
                "ProcessingTimeSeconds": round(processing_time, 2),
                "BatchSucceeded": 1,
                "BatchFailed": 0,
                # DynamoDB update metrics (only when dry_run=False)
                "LabelsUpdated": result.get("updated_count", 0),
                "LabelsSkipped": result.get("skipped_count", 0),
                "LabelsFailed": result.get("failed_count", 0),
                "LabelsNeedsReview": result.get(
                    "needs_review_count", 0
                ),  # Like harmonizer
                # API usage metrics (like harmonizer)
                "LLMCallsTotal": api_metrics.get("llm_calls_total", 0),
                "LLMCallsSuccessful": api_metrics.get(
                    "llm_calls_successful", 0
                ),
                "LLMCallsFailed": api_metrics.get("llm_calls_failed", 0),
                "RateLimitErrors": api_metrics.get("rate_limit_errors", 0),
                "ServerErrors": api_metrics.get("server_errors", 0),
                "RetryAttempts": api_metrics.get("retry_attempts", 0),
                "CircuitBreakerTriggers": api_metrics.get(
                    "circuit_breaker_triggers", 0
                ),
                "TimeoutErrors": api_metrics.get("timeout_errors", 0),
            },
            # IMPORTANT: Keep dimensions LOW cardinality to control costs
            # LabelType has ~15 values (CORE_LABELS)
            # Do NOT add Merchant as dimension (1000s of values = $$$)
            dimensions={"LabelType": primary_label_type},
            # Additional context goes in properties (not dimensions)
            properties={
                "execution_id": execution_id,
                "dry_run": dry_run,
                "batch_file": batch_file,
                "min_confidence": min_confidence,
                "label_type_breakdown": label_type_counts,  # Show breakdown in properties
                "merchant_name": merchant_name,  # Like harmonizer
                "rate_limit_errors": rate_limit_errors,
                "circuit_breaker_triggered": circuit_breaker_triggered,
            },
            units={
                "ProcessingTimeSeconds": "Seconds",
            },
        )

        # Flush LangSmith traces
        flush_langsmith_traces()

        # Return summary
        return {
            "status": "completed",
            "results_path": f"s3://{batch_bucket}/{results_key}",
            "labels_processed": result["labels_processed"],
            "valid_count": result["valid_count"],
            "invalid_count": result["invalid_count"],
            "needs_review_count": result["needs_review_count"],
            "updated_count": result["updated_count"],
            "skipped_count": result["skipped_count"],
            "failed_count": result["failed_count"],
        }

    except Exception as e:
        logger.error(f"Error processing batch: {e}", exc_info=True)

        # Emit failure metrics
        processing_time = time.time() - start_time

        # Try to determine label type from batch_file or use "UNKNOWN"
        label_type = "UNKNOWN"
        if batch_file:
            # Try to extract label type from batch file path or use "MIXED"
            # For now, use "UNKNOWN" for errors
            pass

        emf_metrics.log_metrics(
            metrics={
                "ValidCount": 0,
                "InvalidCount": 0,
                "NeedsReviewCount": 0,
                "LabelsProcessed": 0,
                "ProcessingTimeSeconds": round(processing_time, 2),
                "BatchSucceeded": 0,
                "BatchFailed": 1,
                "LabelsUpdated": 0,
                "LabelsSkipped": 0,
                "LabelsFailed": 0,
            },
            dimensions={"LabelType": label_type},
            properties={
                "execution_id": execution_id,
                "error": str(e),
                "batch_file": batch_file,
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
            "labels_processed": 0,
            "valid_count": 0,
            "invalid_count": 0,
            "needs_review_count": 0,
            "updated_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
        }
