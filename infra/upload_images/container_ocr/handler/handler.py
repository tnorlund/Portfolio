"""
Container-based Lambda handler for OCR processing with integrated merchant validation.

Combines:
1. OCR parsing and storage (from process_ocr_results.py)
2. Merchant validation and embedding (from embed_from_ndjson)
"""

import json
import logging
import os
import sys
import time
import asyncio
from typing import Any, Dict, Optional

from .ocr_processor import OCRProcessor
from .embedding_processor import EmbeddingProcessor
from .metrics import metrics, emf_metrics

# Set up logging - use print for guaranteed output
logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger(__name__)

def _log(msg: str):
    """Log message with immediate flush for CloudWatch visibility."""
    print(f"[HANDLER] {msg}", flush=True)
    logger.info(msg)


def _run_validation_async(
    image_id: str,
    receipt_id: int,
    receipt_lines: Optional[list],
    receipt_words: Optional[list],
    receipt_metadata: Optional[Any],
    ollama_api_key: Optional[str],
    langsmith_api_key: Optional[str],
) -> None:
    """Run LangGraph validation asynchronously (non-blocking).

    This runs in a background thread and doesn't delay the lambda response.
    It validates ReceiptMetadata and auto-corrects if merchant name doesn't match.

    Args:
        image_id: Receipt image identifier
        receipt_id: Receipt identifier
        receipt_lines: Pre-fetched receipt lines
        receipt_words: Pre-fetched receipt words
        receipt_metadata: Pre-fetched ReceiptMetadata (to avoid DynamoDB query)
        ollama_api_key: Ollama API key
        langsmith_api_key: LangSmith API key (optional)
    """
    if not ollama_api_key:
        _log("⚠️ No OLLAMA_API_KEY - skipping validation")
        return

    async def run_validation():
        try:
            from receipt_dynamo import DynamoClient
            from receipt_label.langchain.currency_validation import analyze_receipt_simple
            from receipt_dynamo.constants import CompactionState

            dynamo = DynamoClient(os.environ["DYNAMO_TABLE_NAME"])

            # OPTIMIZATION: Run LangGraph and save labels immediately
            # No need to wait for compaction:
            # - Initial compaction merges S3 deltas (doesn't read ReceiptWordLabels)
            # - ReceiptWordLabel changes are handled by stream processor separately
            # - No race condition possible!
            _log("Starting LangGraph analysis and saving labels...")

            # Run LangGraph and save labels immediately
            result = await analyze_receipt_simple(
                client=dynamo,
                image_id=image_id,
                receipt_id=receipt_id,
                ollama_api_key=ollama_api_key,
                langsmith_api_key=langsmith_api_key,
                save_labels=True,  # Save labels immediately
                dry_run=False,  # Update ReceiptMetadata if mismatch found
                save_dev_state=False,
                # Pass pre-fetched data to skip DynamoDB queries!
                receipt_lines=receipt_lines,
                receipt_words=receipt_words,
                receipt_metadata=receipt_metadata,
            )

            _log(f"✅ LangGraph completed, labels saved")
            _log(f"✅ Validation completed for {image_id}/{receipt_id}")
        except Exception as e:
            _log(f"⚠️ Validation failed: {e}")

    # Run in background thread (Lambda waits for completion before returning)
    import threading

    def run_in_executor():
        # Create new event loop for this thread
        import asyncio
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(run_validation())
        except Exception as e:
            _log(f"⚠️ Validation task failed: {e}")

    # Start in background thread (non-daemon so Lambda waits for it to complete)
    validation_thread = threading.Thread(target=run_in_executor, daemon=False)
    validation_thread.start()
    validation_thread.join()  # Wait for validation to complete
    _log("✅ Validation completed, lambda exiting")


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Process OCR results with integrated merchant validation and embedding.

    Event format (SQS):
        {
            "Records": [{
                "body": "{\"job_id\": \"uuid\", \"image_id\": \"uuid\"}"
            }]
        }
    """
    start_time = time.time()
    record_count = len(event.get('Records', []))

    _log(f"Processing {record_count} OCR records")

    # Collect metrics during processing to batch them via EMF (cost-effective)
    collected_metrics: Dict[str, float] = {
        "UploadLambdaRecordsReceived": record_count,
    }
    metric_dimensions: Dict[str, str] = {}
    image_type_counts: Dict[str, int] = {}

    results = []
    success_count = 0
    error_count = 0
    embedding_count = 0
    ocr_failed_count = 0
    ocr_success_count = 0
    embedding_success_count = 0
    embedding_failed_count = 0
    embedding_skipped_count = 0
    merchant_resolved_count = 0
    total_ocr_duration = 0.0
    total_embedding_duration = 0.0

    for record in event.get("Records", []):
        try:
            result = _process_single_record(record, collected_metrics, image_type_counts)
            results.append(result)

            if result.get("success"):
                success_count += 1
                if result.get("embeddings_created"):
                    embedding_count += 1
            else:
                error_count += 1

            # Aggregate per-record metrics
            if result.get("ocr_failed"):
                ocr_failed_count += 1
            elif result.get("ocr_success"):
                ocr_success_count += 1

            if result.get("embedding_success"):
                embedding_success_count += 1
            elif result.get("embedding_failed"):
                embedding_failed_count += 1
            elif result.get("embedding_skipped"):
                embedding_skipped_count += 1

            if result.get("merchant_resolved"):
                merchant_resolved_count += 1

            if result.get("ocr_duration"):
                total_ocr_duration += result["ocr_duration"]
            if result.get("embedding_duration"):
                total_embedding_duration += result["embedding_duration"]

        except Exception as e:
            _log(f"ERROR: Failed to process record: {e}")
            logger.error(f"Failed to process record: {e}", exc_info=True)
            results.append({"success": False, "error": str(e)})
            error_count += 1

    # Record aggregated metrics
    execution_time = time.time() - start_time
    collected_metrics.update({
        "UploadLambdaExecutionTime": execution_time,
        "UploadLambdaSuccess": success_count,
        "UploadLambdaError": error_count,
        "UploadLambdaEmbeddingsCreated": embedding_count,
        "UploadLambdaOCRFailed": ocr_failed_count,
        "UploadLambdaOCRSuccess": ocr_success_count,
        "UploadLambdaEmbeddingSuccess": embedding_success_count,
        "UploadLambdaEmbeddingFailed": embedding_failed_count,
        "UploadLambdaEmbeddingSkipped": embedding_skipped_count,
        "UploadLambdaMerchantResolved": merchant_resolved_count,
    })

    if ocr_success_count > 0:
        collected_metrics["UploadLambdaOCRDuration"] = total_ocr_duration / ocr_success_count
    if embedding_success_count > 0:
        collected_metrics["UploadLambdaEmbeddingDuration"] = total_embedding_duration / embedding_success_count

    # Log all metrics via EMF in a single log line (no API call cost)
    emf_metrics.log_metrics(
        collected_metrics,
        dimensions=metric_dimensions if metric_dimensions else None,
        properties={
            "image_type_counts": image_type_counts,
            "record_count": record_count,
        },
    )

    _log(f"Completed processing {len(results)} records (success: {success_count}, errors: {error_count}, embeddings: {embedding_count})")
    return {
        "statusCode": 200,
        "body": json.dumps({
            "message": "OCR results processed",
            "processed": len(results),
            "results": results
        })
    }


def _process_single_record(
    record: Dict[str, Any],
    collected_metrics: Dict[str, float],
    image_type_counts: Dict[str, int],
) -> Dict[str, Any]:
    """Process a single SQS record."""
    body = json.loads(record["body"])
    job_id = body["job_id"]
    image_id = body["image_id"]

    _log(f"Processing OCR for image {image_id}, job {job_id}")

    # Initialize OCR processor
    ocr_processor = OCRProcessor(
        table_name=os.environ["DYNAMO_TABLE_NAME"],
        raw_bucket=os.environ["RAW_BUCKET"],
        site_bucket=os.environ["SITE_BUCKET"],
        ocr_job_queue_url=os.environ["OCR_JOB_QUEUE_URL"],
        ocr_results_queue_url=os.environ["OCR_RESULTS_QUEUE_URL"],
    )

    # Step 1: Process OCR (parse, classify, store in DynamoDB)
    ocr_start = time.time()
    ocr_result = ocr_processor.process_ocr_job(image_id, job_id)
    ocr_duration = time.time() - ocr_start

    if not ocr_result.get("success"):
        _log(f"ERROR: OCR processing failed: {ocr_result.get('error')}")
        ocr_result["ocr_failed"] = True
        return ocr_result

    # Track metrics (aggregated, not per-call)
    image_type = ocr_result.get("image_type", "unknown")
    image_type_counts[image_type] = image_type_counts.get(image_type, 0) + 1
    ocr_result["ocr_success"] = True
    ocr_result["ocr_duration"] = ocr_duration

    _log(f"OCR processing completed: image_type={image_type}, receipt_id={ocr_result.get('receipt_id')}")

    # Step 2: Validate merchant and create embeddings
    # Only process embeddings for:
    # - NATIVE receipts (first pass, receipt_id=1)
    # - REFINEMENT jobs (second pass for PHOTO/SCAN, has receipt_id)
    # Do NOT process embeddings for:
    # - PHOTO/SCAN first pass (no receipt-level data yet, receipt_id=None)
    image_type = ocr_result.get("image_type")
    receipt_id = ocr_result.get("receipt_id")

    # Only create embeddings if we have a valid receipt_id
    # NATIVE: receipt_id=1
    # REFINEMENT: receipt_id from the job
    # PHOTO/SCAN first pass: receipt_id=None (skip embeddings)
    if receipt_id is not None and image_type in ["NATIVE", "REFINEMENT"]:
        try:
            _log(f"Initializing embedding processor for {image_type} receipt (receipt_id={receipt_id})")

            embedding_start = time.time()

            # Initialize embedding processor
            embedding_processor = EmbeddingProcessor(
                table_name=os.environ["DYNAMO_TABLE_NAME"],
                chromadb_bucket=os.environ["CHROMADB_BUCKET"],
                chroma_http_endpoint=os.environ.get("CHROMA_HTTP_ENDPOINT"),
                google_places_api_key=os.environ.get("GOOGLE_PLACES_API_KEY"),
                openai_api_key=os.environ.get("OPENAI_API_KEY"),
            )

            # Create embeddings with merchant context
            # Pass receipt lines/words if available from OCR processor
            _log(f"Creating embeddings with lines={ocr_result.get('receipt_lines') is not None}, words={ocr_result.get('receipt_words') is not None}")
            embedding_result = embedding_processor.process_embeddings(
                image_id=image_id,
                receipt_id=receipt_id,
                lines=ocr_result.get("receipt_lines"),
                words=ocr_result.get("receipt_words"),
            )

            embedding_duration = time.time() - embedding_start

            _log(
                f"SUCCESS: Embeddings created for {image_type} receipt: "
                f"image_id={image_id}, receipt_id={receipt_id}, "
                f"merchant={embedding_result.get('merchant_name')}, "
                f"run_id={embedding_result.get('run_id')}"
            )

            # Step 3: Run LangGraph validation
            # OPTIMIZATION: Run LangGraph immediately and save labels
            # No need to wait for compaction - initial compaction only merges S3 deltas
            # ReceiptWordLabel changes trigger separate compaction via stream processor
            try:
                _log("Starting LangGraph validation...")
                _run_validation_async(
                    image_id=image_id,
                    receipt_id=receipt_id,
                    receipt_lines=ocr_result.get("receipt_lines"),
                    receipt_words=ocr_result.get("receipt_words"),
                    receipt_metadata=embedding_result.get("receipt_metadata"),  # Pre-fetched from merchant resolution
                    ollama_api_key=os.environ.get("OLLAMA_API_KEY"),
                    langsmith_api_key=os.environ.get("LANGCHAIN_API_KEY"),
                )
            except Exception as val_error:
                _log(f"⚠️ Validation error (non-critical): {val_error}")
                # Don't fail the lambda - validation is optional

            # Track metrics (aggregated, not per-call) - add to return dict
            return {
                "success": True,
                "image_id": image_id,
                "receipt_id": receipt_id,
                "image_type": image_type,
                "merchant_name": embedding_result.get("merchant_name"),
                "run_id": embedding_result.get("run_id"),
                "embeddings_created": True,
                "embedding_success": True,
                "embedding_duration": embedding_duration,
                "merchant_resolved": bool(embedding_result.get("merchant_name")),
            }

        except Exception as e:
            _log(f"ERROR: Merchant validation/embedding failed for {image_type}: {e}")
            logger.error(
                f"Merchant validation/embedding failed for {image_type}: {e}",
                exc_info=True
            )

            # Don't fail the whole job - OCR data is still stored
            # Track error (aggregated, not per-call) - add to return dict
            return {
                "success": True,
                "image_id": image_id,
                "receipt_id": receipt_id,
                "image_type": image_type,
                "embeddings_created": False,
                "embedding_error": str(e),
                "embedding_failed": True,
            }

    # For PHOTO/SCAN first pass, just return the OCR result
    # Embeddings will be created when REFINEMENT jobs run
    ocr_result["embedding_skipped"] = True
    _log(
        f"Skipping embeddings for {image_type} (receipt_id={receipt_id}). "
        f"Will process embeddings during REFINEMENT jobs."
    )
    return ocr_result

