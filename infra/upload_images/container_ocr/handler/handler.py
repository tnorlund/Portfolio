"""
Container-based Lambda handler for OCR processing with integrated merchant
validation.

Combines:
1. OCR parsing and storage (from process_ocr_results.py)
2. Merchant validation and embedding

Updated: 2026-01-14 - Fixed entity serialization: use asdict() and **unpacking instead of to_dict/from_dict
Updated: 2026-01-15 - Added chroma_label_validation trace for visibility into Phase 2 parallelism
Updated: 2026-01-18 - Force rebuild with latest receipt_chroma and receipt_upload packages
"""

import json
import logging
import os
import sys
import time
from typing import Any, Dict, cast

from receipt_upload.label_validation.langsmith_logging import flush_traces
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
    # Messages to redrive (the llm-validation mapping reports these so SQS
    # retries / DLQs them instead of silently deleting on a swallowed error).
    batch_item_failures: list[Dict[str, str]] = []
    llm_validation_failures = 0

    for record in event.get("Records", []):
        # Two queues feed this Lambda. An async LLM-validation message is a
        # small pointer ({s3_bucket, s3_key, image_id, receipt_id}) with no
        # "job_id"; route it to the deferred grok runner. Everything else is
        # an OCR job.
        is_llm_record = _is_llm_validation_record(record)
        try:
            if is_llm_record:
                result = _process_llm_validation_record(record)
                results.append(result)
                success_count += 1
                continue

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
            if is_llm_record:
                # Report for redrive instead of swallowing — otherwise the
                # message is deleted and the labels stay PENDING forever.
                llm_validation_failures += 1
                mid = record.get("messageId")
                if mid:
                    batch_item_failures.append({"itemIdentifier": mid})

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
            # Deferred-grok consumer failures (redriven to DLQ). Alarm on >0 so a
            # grok/OpenRouter outage or bad payload is never silently dropped.
            "UploadLambdaLLMValidationFailed": llm_validation_failures,
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
    # Serialize results for JSON response - entity objects aren't JSON serializable
    serializable_results = []
    for result in results:
        serialized = {}
        for key, value in result.items():
            # Skip entity lists (receipt_lines, receipt_words) - they're not needed in response
            if key in ("receipt_lines", "receipt_words"):
                continue
            serialized[key] = value
        serializable_results.append(serialized)

    # Flush Langsmith traces before Lambda terminates
    # This ensures all validation/merchant resolution decisions are logged
    flush_traces()

    # The llm-validation mapping has ReportBatchItemFailures enabled, so SQS
    # redrives any messageId returned here (and DLQs it after maxReceiveCount).
    # The OCR-results mapping does not enable it, so this field is ignored there.
    return {
        "statusCode": 200,
        "batchItemFailures": batch_item_failures,
        "body": json.dumps(
            {
                "message": "OCR results processed",
                "processed": len(results),
                "results": serializable_results,
            }
        ),
    }


def _emit_section_observability(
    image_id: str, receipt_id: int, embedding_result: Dict[str, Any]
) -> None:
    """Emit per-receipt row/section/verification metrics via EMF.

    Metrics-only: pulls the observability keys the lines pipeline returns
    (row provenance, deterministic section proposals, Chroma verification
    outcomes) into one alarmable EMF log line. Never raises and never
    changes pipeline behavior.
    """
    try:
        metric_map = {
            "UploadLambdaReceiptRows": embedding_result.get("row_count"),
            "UploadLambdaSectionsProposed": embedding_result.get(
                "section_proposed_count"
            ),
            "UploadLambdaSectionMeanConfidence": embedding_result.get(
                "section_mean_confidence"
            ),
            "UploadLambdaSectionAgreed": embedding_result.get(
                "verification_agreed_count"
            ),
            "UploadLambdaSectionDisagreed": embedding_result.get(
                "verification_disagreement_count"
            ),
            "UploadLambdaSectionAbstained": embedding_result.get(
                "verification_abstained_count"
            ),
        }
        row_source = embedding_result.get("row_source")
        if row_source is not None:
            # 1 when ingest did not persist rows and the lines pipeline fell
            # back to in-process reconstruction (legacy/dev replays). Alarm on
            # sustained >0 for fresh uploads.
            metric_map["UploadLambdaReceiptRowsReconstructed"] = (
                1 if row_source == "reconstructed" else 0
            )
        metrics = {
            name: float(value)
            for name, value in metric_map.items()
            if value is not None
        }
        if not metrics:
            return
        emf_metrics.log_metrics(
            metrics,
            properties={
                "image_id": image_id,
                "receipt_id": receipt_id,
                "row_source": row_source,
                "verification_error": embedding_result.get(
                    "verification_error"
                ),
            },
            # Mean confidence is a 0-1 ratio, not a count.
            units={"UploadLambdaSectionMeanConfidence": "None"},
        )
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception(
            "Failed to emit section observability metrics for %s#%s",
            image_id,
            receipt_id,
        )


def _is_llm_validation_record(record: Dict[str, Any]) -> bool:
    """True for deferred-LLM-validation messages (vs. OCR jobs).

    Route by the SOURCE QUEUE (eventSourceARN) — authoritative and immune to a
    malformed body being misclassified (and then silently dropped) on the wrong
    path. Fall back to body-shape only if the ARN is absent.
    """
    arn = record.get("eventSourceARN") or record.get("eventSourceArn") or ""
    if arn:
        return arn.endswith("-llm-validation-queue")
    try:
        body = json.loads(record["body"])
    except (KeyError, ValueError):
        return False
    return "s3_key" in body and "job_id" not in body


def _process_llm_validation_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Run deferred grok validation for one staged payload.

    The producer (words pipeline) staged a self-contained JSON payload on S3 and
    enqueued a pointer. We fetch it, run the LLM validator, and persist the
    decisions to DynamoDB — exactly what the synchronous path would have done,
    just off the upload critical path.
    """
    import boto3
    from receipt_dynamo import DynamoClient

    from receipt_upload.label_validation.llm_runner import apply_async_payload

    body = json.loads(record["body"])
    bucket = body["s3_bucket"]
    key = body["s3_key"]
    image_id = body.get("image_id")
    receipt_id = body.get("receipt_id")
    _log(
        "Async LLM validation: image=%s receipt=%s s3=%s/%s",
        image_id,
        receipt_id,
        bucket,
        key,
    )

    s3 = boto3.client("s3")
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
    except s3.exceptions.NoSuchKey:
        # Payload already deleted by a prior successful processing of this
        # message — a redelivery of an already-settled record. No-op success so
        # it does NOT count as a failure / re-DLQ.
        _log(
            "Async LLM validation: staged payload already gone (%s/%s) — "
            "treating redelivery as no-op success",
            bucket,
            key,
        )
        return {"success": True, "llm_validated": 0, "image_id": image_id}
    payload = json.loads(obj["Body"].read())

    dynamo = DynamoClient(os.environ["DYNAMO_TABLE_NAME"])
    # grok decisions -> DynamoDB (raises on LLM failure -> SQS redrive/DLQ).
    validated = apply_async_payload(payload, dynamo)
    _log(
        "Async LLM validation complete: image=%s validated=%s",
        image_id,
        validated,
    )

    # NOTE: grok corrections land in DynamoDB (the source of truth); Chroma's
    # label metadata for these words converges on the next compaction. Pushing
    # corrections into Chroma immediately via a corrective delta is deferred to
    # the words-compaction reliability work (#990) — it overwhelms the current
    # words-compaction subsystem, so it lands stacked on that fix.

    # Best-effort cleanup of the staged payload (idempotent if already gone).
    try:
        s3.delete_object(Bucket=bucket, Key=key)
    except Exception:  # pylint: disable=broad-exception-caught
        pass

    return {"success": True, "llm_validated": validated, "image_id": image_id}


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
        chromadb_bucket=os.environ.get("CHROMADB_BUCKET", ""),
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

    # For Swift single-pass with multiple receipts, process all of them
    receipt_ids = ocr_result.get(
        "receipt_ids", [receipt_id] if receipt_id else []
    )
    per_receipt_data = ocr_result.get("per_receipt_data", {})

    # Only create embeddings if we have valid receipt_id(s)
    # NATIVE: receipt_id=1
    # REFINEMENT: receipt_id from the job
    # Swift single-pass: all receipt_ids from the image
    # Legacy PHOTO/SCAN first pass: receipt_id=None (skip embeddings)
    should_create_embeddings = receipt_id is not None and (
        image_type in ["NATIVE", "REFINEMENT"] or is_swift_single_pass
    )
    if should_create_embeddings:
        try:
            _log(
                "Initializing merchant-resolving embedding processor for %s "
                "image with %s receipt(s)",
                image_type,
                len(receipt_ids),
            )

            embedding_start = time.time()

            # Initialize merchant-resolving embedding processor once
            # This processor generates embeddings, resolves merchant info, and
            # enriches the receipt in DynamoDB
            embedding_processor = MerchantResolvingEmbeddingProcessor(
                table_name=os.environ["DYNAMO_TABLE_NAME"],
                chromadb_bucket=os.environ["CHROMADB_BUCKET"],
                google_places_api_key=os.environ.get("GOOGLE_PLACES_API_KEY"),
                openai_api_key=os.environ.get("OPENAI_API_KEY"),
            )

            # Process each receipt for merchant resolution and embeddings
            all_embedding_results = []
            total_merchants_found = 0

            for rid in receipt_ids:
                # Get per-receipt lines/words if available, otherwise use combined
                receipt_data = per_receipt_data.get(rid, {})
                lines = receipt_data.get("lines") or ocr_result.get(
                    "receipt_lines"
                )
                words = receipt_data.get("words") or ocr_result.get(
                    "receipt_words"
                )

                _log(
                    "Processing embeddings for receipt %s: lines=%s, words=%s",
                    rid,
                    lines is not None,
                    words is not None,
                )

                try:
                    embedding_result = embedding_processor.process_embeddings(
                        image_id=image_id,
                        receipt_id=rid,
                        lines=lines,
                        words=words,
                    )

                    _emit_section_observability(
                        image_id, rid, embedding_result
                    )

                    merchant_found = embedding_result.get(
                        "merchant_found", False
                    )
                    if merchant_found:
                        total_merchants_found += 1

                    _log(
                        "SUCCESS: Embeddings created for receipt %s: "
                        "merchant_found=%s, merchant_name=%s",
                        rid,
                        merchant_found,
                        embedding_result.get("merchant_name"),
                    )

                    all_embedding_results.append(
                        {
                            "receipt_id": rid,
                            "success": True,
                            "merchant_found": merchant_found,
                            "merchant_name": embedding_result.get(
                                "merchant_name"
                            ),
                            "merchant_place_id": embedding_result.get(
                                "merchant_place_id"
                            ),
                        }
                    )

                except Exception as receipt_exc:
                    _log(
                        "ERROR: Embedding failed for receipt %s: %s",
                        rid,
                        receipt_exc,
                    )
                    all_embedding_results.append(
                        {
                            "receipt_id": rid,
                            "success": False,
                            "error": str(receipt_exc),
                        }
                    )

            embedding_duration = time.time() - embedding_start

            # Use first receipt's result for backward compatibility
            first_result = (
                all_embedding_results[0] if all_embedding_results else {}
            )

            _log(
                "SUCCESS: Processed %s receipts for %s image: "
                "merchants_found=%s, duration=%.2fs",
                len(receipt_ids),
                image_type,
                total_merchants_found,
                embedding_duration,
            )

            # Track metrics (aggregated, not per-call) - add to return dict
            return {
                "success": True,
                "image_id": image_id,
                "receipt_id": receipt_id,
                "receipt_ids": receipt_ids,
                "receipts_processed": len(receipt_ids),
                "image_type": image_type,
                "embeddings_created": True,
                "embedding_success": True,
                "embedding_duration": embedding_duration,
                "merchant_found": first_result.get("merchant_found", False),
                "merchant_name": first_result.get("merchant_name"),
                "merchant_place_id": first_result.get("merchant_place_id"),
                "merchants_found_count": total_merchants_found,
                "all_embedding_results": all_embedding_results,
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
