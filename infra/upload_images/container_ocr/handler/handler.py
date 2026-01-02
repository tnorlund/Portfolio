"""
Container-based Lambda handler for OCR processing with integrated merchant
validation.

Combines:
1. OCR parsing and storage (from process_ocr_results.py)
2. Merchant validation and embedding (from embed_from_ndjson)
"""

import json
import logging
import os
import sys
import time
from typing import Any, Dict, cast

from receipt_upload.merchant_resolution import (
    MerchantResolvingEmbeddingProcessor,
)

from .metrics import emf_metrics
from .ocr_processor import OCRProcessor

# Set up logging - use print for guaranteed output
logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger(__name__)


def _log(msg: str, *args: object) -> None:
    """Log message with immediate flush for CloudWatch visibility."""
    formatted = msg % args if args else msg
    print(f"[HANDLER] {formatted}", flush=True)
    logger.info(msg, *args)


def lambda_handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
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
    record_count = len(event.get("Records", []))

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
    total_ocr_duration = 0.0
    total_embedding_duration = 0.0

    for record in event.get("Records", []):
        try:
            result = _process_single_record(record, image_type_counts)
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

            if result.get("ocr_duration"):
                total_ocr_duration += result["ocr_duration"]
            if result.get("embedding_duration"):
                total_embedding_duration += result["embedding_duration"]

        except Exception as exc:  # pylint: disable=broad-exception-caught
            _log(f"ERROR: Failed to process record: {exc}")
            logger.error(
                "Failed to process record: %s",
                exc,
                exc_info=True,
            )
            results.append({"success": False, "error": str(exc)})
            error_count += 1

    # Record aggregated metrics
    execution_time = time.time() - start_time
    collected_metrics.update(
        {
            "UploadLambdaExecutionTime": execution_time,
            "UploadLambdaSuccess": success_count,
            "UploadLambdaError": error_count,
            "UploadLambdaEmbeddingsCreated": embedding_count,
            "UploadLambdaOCRFailed": ocr_failed_count,
            "UploadLambdaOCRSuccess": ocr_success_count,
            "UploadLambdaEmbeddingSuccess": embedding_success_count,
            "UploadLambdaEmbeddingFailed": embedding_failed_count,
            "UploadLambdaEmbeddingSkipped": embedding_skipped_count,
        }
    )

    if ocr_success_count > 0:
        collected_metrics["UploadLambdaOCRDuration"] = (
            total_ocr_duration / ocr_success_count
        )
    if embedding_success_count > 0:
        collected_metrics["UploadLambdaEmbeddingDuration"] = (
            total_embedding_duration / embedding_success_count
        )

    # Log all metrics via EMF in a single log line (no API call cost)
    emf_metrics.log_metrics(
        collected_metrics,
        dimensions=metric_dimensions if metric_dimensions else None,
        properties={
            "image_type_counts": image_type_counts,
            "record_count": record_count,
        },
    )

    _log(
        "Completed processing %s records (success: %s, errors: %s, "
        "embeddings: %s)",
        len(results),
        success_count,
        error_count,
        embedding_count,
    )
    return {
        "statusCode": 200,
        "body": json.dumps(
            {
                "message": "OCR results processed",
                "processed": len(results),
                "results": results,
            }
        ),
    }


def _process_single_record(
    record: Dict[str, Any],
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

    _log(
        f"OCR processing completed: image_type={image_type}, "
        f"receipt_id={ocr_result.get('receipt_id')}"
    )

    # Step 2: Validate merchant and create embeddings
    # Only process embeddings for:
    # - NATIVE receipts (first pass, receipt_id=1)
    # - REFINEMENT jobs (second pass for PHOTO/SCAN, has receipt_id)
    # - Swift single-pass results (has receipt_id and swift_single_pass=True)
    # Do NOT process embeddings for:
    # - Legacy PHOTO/SCAN first pass (no receipt-level data yet,
    #   receipt_id=None)
    image_type = ocr_result.get("image_type")
    receipt_id = ocr_result.get("receipt_id")
    is_swift_single_pass = ocr_result.get("swift_single_pass", False)

    # Only create embeddings if we have a valid receipt_id
    # NATIVE: receipt_id=1
    # REFINEMENT: receipt_id from the job
    # Swift single-pass: receipt_id from first receipt, any image_type
    # Legacy PHOTO/SCAN first pass: receipt_id=None (skip embeddings)
    should_create_embeddings = receipt_id is not None and (
        image_type in ["NATIVE", "REFINEMENT"] or is_swift_single_pass
    )
    if should_create_embeddings:
        try:
            _log(
                "Initializing merchant-resolving embedding processor for %s "
                "receipt (receipt_id=%s)",
                image_type,
                receipt_id,
            )

            embedding_start = time.time()

            # Initialize merchant-resolving embedding processor
            # This processor generates embeddings, resolves merchant info, and
            # enriches the receipt in DynamoDB
            embedding_processor = MerchantResolvingEmbeddingProcessor(
                table_name=os.environ["DYNAMO_TABLE_NAME"],
                chromadb_bucket=os.environ["CHROMADB_BUCKET"],
                chroma_http_endpoint=os.environ.get("CHROMA_HTTP_ENDPOINT"),
                google_places_api_key=os.environ.get("GOOGLE_PLACES_API_KEY"),
                openai_api_key=os.environ.get("OPENAI_API_KEY"),
                lines_queue_url=os.environ.get("CHROMADB_LINES_QUEUE_URL"),
                words_queue_url=os.environ.get("CHROMADB_WORDS_QUEUE_URL"),
            )

            # Create embeddings with merchant resolution
            # Pass receipt lines/words if available from OCR processor
            _log(
                "Creating embeddings with merchant resolution: lines=%s, "
                "words=%s",
                ocr_result.get("receipt_lines") is not None,
                ocr_result.get("receipt_words") is not None,
            )
            receipt_id_value = cast(int, receipt_id)
            embedding_result = embedding_processor.process_embeddings(
                image_id=image_id,
                receipt_id=receipt_id_value,
                lines=ocr_result.get("receipt_lines"),
                words=ocr_result.get("receipt_words"),
            )

            embedding_duration = time.time() - embedding_start

            merchant_found = embedding_result.get("merchant_found", False)
            merchant_name = embedding_result.get("merchant_name")
            merchant_tier = embedding_result.get("merchant_resolution_tier")

            _log(
                "SUCCESS: Embeddings created for %s receipt: image_id=%s, "
                "receipt_id=%s, run_id=%s, merchant_found=%s, "
                "merchant_name=%s, resolution_tier=%s",
                image_type,
                image_id,
                receipt_id,
                embedding_result.get("run_id"),
                merchant_found,
                merchant_name,
                merchant_tier,
            )

            # Track metrics (aggregated, not per-call) - add to return dict
            return {
                "success": True,
                "image_id": image_id,
                "receipt_id": receipt_id,
                "image_type": image_type,
                "run_id": embedding_result.get("run_id"),
                "embeddings_created": True,
                "embedding_success": True,
                "embedding_duration": embedding_duration,
                "merchant_found": merchant_found,
                "merchant_name": merchant_name,
                "merchant_place_id": embedding_result.get("merchant_place_id"),
                "merchant_resolution_tier": merchant_tier,
                "merchant_confidence": embedding_result.get(
                    "merchant_confidence"
                ),
            }

        except Exception as exc:  # pylint: disable=broad-exception-caught
            _log(
                "ERROR: Merchant validation/embedding failed for %s: %s",
                image_type,
                exc,
            )
            logger.error(
                "Merchant validation/embedding failed for %s: %s",
                image_type,
                exc,
                exc_info=True,
            )

            # Don't fail the whole job - OCR data is still stored
            # Track error (aggregated, not per-call) - add to return dict
            return {
                "success": True,
                "image_id": image_id,
                "receipt_id": receipt_id,
                "image_type": image_type,
                "embeddings_created": False,
                "embedding_error": str(exc),
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
