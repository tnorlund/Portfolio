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
from .metrics import metrics

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
    run_id: str,
    receipt_lines: Optional[list],
    receipt_words: Optional[list],
    receipt_metadata: Optional[Any],
    ollama_api_key: Optional[str],
    langsmith_api_key: Optional[str],
) -> None:
    """Run LangGraph validation asynchronously (non-blocking).
    
    This runs in the background and doesn't delay the lambda response.
    It validates ReceiptMetadata and auto-corrects if merchant name doesn't match.
    
    IMPORTANT: Waits for initial compaction to complete before creating ReceiptWordLabels
    to avoid race conditions.
    
    Args:
        image_id: Receipt image identifier
        receipt_id: Receipt identifier
        run_id: Compaction run ID to track completion
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
            
            # OPTIMIZATION: Start LangGraph immediately (LLM processing takes ~20-30s)
            # We'll run it without writing labels, then wait for compaction before writing
            _log("Starting LangGraph analysis (running in parallel with compaction)...")
            
            # Run LangGraph analysis WITHOUT saving labels (dry_run=True)
            # This processes the receipt text with LLM while compaction is running
            result = await analyze_receipt_simple(
                client=dynamo,
                image_id=image_id,
                receipt_id=receipt_id,
                ollama_api_key=ollama_api_key,
                langsmith_api_key=langsmith_api_key,
                save_labels=False,  # Don't save labels yet - will save manually after compaction
                dry_run=True,  # Don't update ReceiptMetadata yet
                save_dev_state=False,
                # Pass pre-fetched data to skip DynamoDB queries!
                receipt_lines=receipt_lines,
                receipt_words=receipt_words,
                receipt_metadata=receipt_metadata,
            )
            
            _log(f"✅ LangGraph analysis completed, extracted {len(result.discovered_labels)} labels")
            _log("Waiting for compaction to complete before writing labels...")
            
            # NOW wait for compaction to complete before writing labels
            max_wait_seconds = 120
            poll_interval_seconds = 2
            waited_seconds = 0
            
            while waited_seconds < max_wait_seconds:
                try:
                    compaction_run = dynamo.get_compaction_run(image_id, receipt_id, run_id)
                    if compaction_run:
                        lines_completed = compaction_run.lines_state == CompactionState.COMPLETED.value
                        words_completed = compaction_run.words_state == CompactionState.COMPLETED.value
                        
                        if lines_completed and words_completed:
                            _log(f"✅ Compaction completed, now writing labels (waited {waited_seconds}s)")
                            break
                        else:
                            _log(f"⏳ Still waiting: lines={compaction_run.lines_state}, words={compaction_run.words_state}")
                    else:
                        _log(f"⚠️ Compaction run not found, proceeding anyway")
                        break
                except Exception as e:
                    _log(f"⚠️ Error checking compaction state: {e}")
                    if waited_seconds > 60:
                        _log(f"⚠️ Continuing after {waited_seconds}s despite errors")
                        break
                
                await asyncio.sleep(poll_interval_seconds)
                waited_seconds += poll_interval_seconds
            
            if waited_seconds >= max_wait_seconds:
                _log(f"⚠️ Timeout waiting for compaction, proceeding anyway")
            
            # Now safe to write labels - compaction is done
            _log("Writing ReceiptWordLabels to DynamoDB...")
            
            # Save the labels from the first run (we already ran LangGraph with dry_run=True)
            from receipt_label.langchain.currency_validation import save_receipt_word_labels
            
            to_add = getattr(result, 'receipt_word_labels_to_add', [])
            to_update = getattr(result, 'receipt_word_labels_to_update', [])
            
            if to_add or to_update:
                await save_receipt_word_labels(
                    client=dynamo,
                    receipt_word_labels_to_add=to_add,
                    receipt_word_labels_to_update=to_update,
                    dry_run=False,
                )
                _log(f"✅ Saved {len(to_add)} new labels and updated {len(to_update)} existing labels")
            
            # Update ReceiptMetadata if validation found a mismatch
            if hasattr(result, 'metadata_validation') and result.metadata_validation:
                metadata_validation = result.metadata_validation
                if metadata_validation.get("requires_metadata_update"):
                    _log("Updating ReceiptMetadata due to validation mismatch...")
                    if receipt_metadata:
                        original_name = metadata_validation.get("original_merchant_name", "")
                        corrected_name = metadata_validation.get("corrected_merchant_name", "")
                        
                        if corrected_name and corrected_name != receipt_metadata.merchant_name:
                            receipt_metadata.merchant_name = corrected_name
                            receipt_metadata.reasoning = (
                                f"Auto-corrected by LangGraph validation. "
                                f"Original: '{original_name}', Corrected: '{corrected_name}'. "
                                f"Receipt text is authoritative source."
                            )
                            dynamo.update_receipt_metadata(receipt_metadata)
                            _log(f"✅ Updated ReceiptMetadata in DynamoDB")
            
            _log(f"✅ Validation completed for {image_id}/{receipt_id}")
        except Exception as e:
            _log(f"⚠️ Validation failed: {e}")
    
    # Run in background - don't wait
    asyncio.create_task(run_validation())
    _log("Validation started in background (processing while compaction runs, will wait to write)")


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
    metrics.gauge("UploadLambdaRecordsReceived", record_count)
    
    results = []
    success_count = 0
    error_count = 0
    embedding_count = 0
    
    for record in event.get("Records", []):
        try:
            result = _process_single_record(record)
            results.append(result)
            
            if result.get("success"):
                success_count += 1
                if result.get("embeddings_created"):
                    embedding_count += 1
            else:
                error_count += 1
                
        except Exception as e:
            _log(f"ERROR: Failed to process record: {e}")
            logger.error(f"Failed to process record: {e}", exc_info=True)
            results.append({"success": False, "error": str(e)})
            error_count += 1
    
    # Record metrics
    execution_time = time.time() - start_time
    metrics.timer("UploadLambdaExecutionTime", execution_time, unit="Seconds")
    metrics.count("UploadLambdaSuccess", success_count)
    if error_count > 0:
        metrics.count("UploadLambdaError", error_count)
    if embedding_count > 0:
        metrics.count("UploadLambdaEmbeddingsCreated", embedding_count)
    
    _log(f"Completed processing {len(results)} records (success: {success_count}, errors: {error_count}, embeddings: {embedding_count})")
    return {
        "statusCode": 200,
        "body": json.dumps({
            "message": "OCR results processed",
            "processed": len(results),
            "results": results
        })
    }


def _process_single_record(record: Dict[str, Any]) -> Dict[str, Any]:
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
        metrics.count("UploadLambdaOCRFailed", 1)
        return ocr_result
    
    # Record metrics by image type
    image_type = ocr_result.get("image_type", "unknown")
    metrics.timer("UploadLambdaOCRDuration", ocr_duration, unit="Seconds", 
                  dimensions={"image_type": image_type})
    metrics.count("UploadLambdaOCRSuccess", 1, dimensions={"image_type": image_type})
    
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
            
            # Record metrics
            metrics.timer("UploadLambdaEmbeddingDuration", embedding_duration, unit="Seconds",
                         dimensions={"image_type": image_type})
            metrics.count("UploadLambdaEmbeddingSuccess", 1, dimensions={"image_type": image_type})
            if embedding_result.get("merchant_name"):
                metrics.count("UploadLambdaMerchantResolved", 1, dimensions={"image_type": image_type})
            
            _log(
                f"SUCCESS: Embeddings created for {image_type} receipt: "
                f"image_id={image_id}, receipt_id={receipt_id}, "
                f"merchant={embedding_result.get('merchant_name')}, "
                f"run_id={embedding_result.get('run_id')}"
            )
            
            # Step 3: Run LangGraph validation (parallel processing, deferred writes)
            # OPTIMIZATION: Start LangGraph immediately, but wait for compaction before writing
            # LangGraph LLM processing takes ~20-30s and runs while compaction is happening
            # We only wait before writing to DynamoDB to avoid race conditions
            try:
                _log("Starting ReceiptMetadata validation (waiting for compaction...)...")
                _run_validation_async(
                    image_id=image_id,
                    receipt_id=receipt_id,
                    run_id=embedding_result.get("run_id"),  # Track compaction completion
                    receipt_lines=ocr_result.get("receipt_lines"),
                    receipt_words=ocr_result.get("receipt_words"),
                    receipt_metadata=embedding_result.get("receipt_metadata"),  # Pre-fetched from merchant resolution
                    ollama_api_key=os.environ.get("OLLAMA_API_KEY"),
                    langsmith_api_key=os.environ.get("LANGCHAIN_API_KEY"),
                )
            except Exception as val_error:
                _log(f"⚠️ Validation error (non-critical): {val_error}")
                # Don't fail the lambda - validation is optional
            
            return {
                "success": True,
                "image_id": image_id,
                "receipt_id": receipt_id,
                "image_type": image_type,
                "merchant_name": embedding_result.get("merchant_name"),
                "run_id": embedding_result.get("run_id"),
                "embeddings_created": True,
            }
            
        except Exception as e:
            _log(f"ERROR: Merchant validation/embedding failed for {image_type}: {e}")
            logger.error(
                f"Merchant validation/embedding failed for {image_type}: {e}",
                exc_info=True
            )
            
            # Record error metric
            metrics.count("UploadLambdaEmbeddingFailed", 1, dimensions={"image_type": image_type})
            
            # Don't fail the whole job - OCR data is still stored
            return {
                "success": True,
                "image_id": image_id,
                "receipt_id": receipt_id,
                "image_type": image_type,
                "embeddings_created": False,
                "embedding_error": str(e),
            }
    
    # For PHOTO/SCAN first pass, just return the OCR result
    # Embeddings will be created when REFINEMENT jobs run
    metrics.count("UploadLambdaEmbeddingSkipped", 1, dimensions={"image_type": image_type})
    _log(
        f"Skipping embeddings for {image_type} (receipt_id={receipt_id}). "
        f"Will process embeddings during REFINEMENT jobs."
    )
    return ocr_result

