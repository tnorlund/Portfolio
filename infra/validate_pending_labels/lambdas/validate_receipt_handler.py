"""Container Lambda handler for validating PENDING labels for a single receipt."""

import asyncio
import json
import logging
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict

import boto3
from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import ValidationStatus
from receipt_label.utils.chroma_s3_helpers import download_snapshot_atomic
from receipt_label.vector_store.client.chromadb_client import ChromaDBClient

# Import EMF metrics utility
# Utils directory is in the same directory as the handler (lambdas/utils/)
from utils.emf_metrics import emf_metrics

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    force=True,  # Force reconfiguration in case logging was already configured
)
logger = logging.getLogger(__name__)
# Ensure logs are flushed immediately
if logging.getLogger().handlers:
    logging.getLogger().handlers[0].stream = sys.stdout


async def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Validate PENDING labels for a single receipt using LangGraph + CoVe + ChromaDB.

    Input:
        {
            "index": 0,
            "manifest_s3_key": "...",
            "manifest_s3_bucket": "...",
            "execution_id": "..."
        }

    Returns:
        {
            "success": True,
            "index": 0,
            "image_id": "...",
            "receipt_id": 1,
            "labels_validated": 5,
            "labels_updated": 3,
            "labels_added": 2
        }
    """
    s3_client = boto3.client("s3")
    dynamo = DynamoClient(os.environ["DYNAMODB_TABLE_NAME"])

    index = event["index"]
    manifest_s3_key = event["manifest_s3_key"]
    manifest_s3_bucket = event["manifest_s3_bucket"]

    chromadb_temp_dir = None
    chroma_client = None

    # Track timing for metrics
    start_time = time.time()

    # Collect metrics during processing
    collected_metrics: Dict[str, float] = {}

    try:
        # Download manifest from S3
        logger.info("Downloading manifest from S3: %s/%s", manifest_s3_bucket, manifest_s3_key)
        with tempfile.NamedTemporaryFile(mode="r", suffix=".json", delete=False) as tmp_manifest:
            s3_client.download_file(manifest_s3_bucket, manifest_s3_key, tmp_manifest.name)
            with open(tmp_manifest.name, "r") as f:
                manifest = json.load(f)

        # Look up receipt info using index
        receipt_info = manifest["receipts"][index]
        image_id = receipt_info["image_id"]
        receipt_id = receipt_info["receipt_id"]

        logger.info(
            "Processing receipt: image_id=%s, receipt_id=%s",
            image_id,
            receipt_id,
        )

        # Fetch receipt data from DynamoDB
        logger.info("Fetching receipt data from DynamoDB...")
        lines = dynamo.list_receipt_lines_from_receipt(image_id, receipt_id)
        words = dynamo.list_receipt_words_from_receipt(image_id, receipt_id)
        metadata = dynamo.get_receipt_metadata(image_id, receipt_id)

        # Get status from manifest (passed from ListPendingLabels)
        status_str = manifest.get("status", "PENDING").upper()
        try:
            target_status = ValidationStatus[status_str]
        except KeyError:
            logger.warning("Invalid status in manifest: %s, defaulting to PENDING", status_str)
            target_status = ValidationStatus.PENDING

        # Get all labels for this receipt and filter by target status
        # This is more efficient than fetching all labels across all receipts
        all_receipt_labels, _ = dynamo.list_receipt_word_labels_for_receipt(
            image_id=image_id,
            receipt_id=receipt_id,
        )

        # Debug: Log all label statuses
        status_counts = {}
        for label in all_receipt_labels:
            status = str(label.validation_status)
            status_counts[status] = status_counts.get(status, 0) + 1
        logger.info("All labels for receipt: %s", status_counts)
        logger.info("Looking for status: %s (value: %s)", status_str, target_status.value)

        pending_labels = [
            label
            for label in all_receipt_labels
            if label.validation_status == target_status.value
        ]

        logger.info(
            "Fetched receipt data: %d lines, %d words, %d total labels, %d labels with status %s",
            len(lines),
            len(words),
            len(all_receipt_labels),
            len(pending_labels),
            status_str,
        )

        if not pending_labels:
            logger.info("No labels to validate for this receipt")
            return {
                "success": True,
                "index": index,
                "image_id": image_id,
                "receipt_id": receipt_id,
                "labels_validated": 0,
                "labels_invalidated": 0,
                "labels_needs_review": 0,
                "total_pending": 0,
            }

        # For INVALID labels, use CoVe-only approach (like dev script)
        # For PENDING labels, use three-tier validation (rule-based → ChromaDB → CoVe)
        use_cove_only = (target_status == ValidationStatus.INVALID)

        if use_cove_only:
            # Filter to CORE_LABELS only (like dev script)
            from receipt_label.constants import CORE_LABELS
            core_label_keys = set(CORE_LABELS.keys())
            core_labels = [
                label for label in pending_labels
                if str(label.label).upper() in core_label_keys
            ]

            if not core_labels:
                logger.info("No CORE_LABELS to validate for this receipt")
                return {
                    "success": True,
                    "index": index,
                    "image_id": image_id,
                    "receipt_id": receipt_id,
                    "labels_validated": 0,
                    "labels_invalidated": 0,
                    "labels_needs_review": 0,
                    "total_pending": len(pending_labels),
                }

            logger.info("Filtered to %d CORE_LABELS (out of %d total INVALID labels)", len(core_labels), len(pending_labels))

            # Convert INVALID → PENDING for CoVe validation (like dev script)
            for label in core_labels:
                label.validation_status = ValidationStatus.PENDING.value

            # Use only these labels for validation
            pending_labels = core_labels

        # Download ChromaDB snapshot from S3
        chromadb_bucket = os.environ.get("CHROMADB_BUCKET")
        chromadb_download_duration_ms = 0
        if chromadb_bucket:
            try:
                chromadb_temp_dir = tempfile.mkdtemp(prefix="chromadb_")
                logger.info("Downloading ChromaDB snapshot from S3...")
                chromadb_download_start = time.time()

                words_download = download_snapshot_atomic(
                    bucket=chromadb_bucket,
                    collection="words",
                    local_path=os.path.join(chromadb_temp_dir, "words"),
                    verify_integrity=True,
                )

                chromadb_download_duration_ms = (time.time() - chromadb_download_start) * 1000

                if words_download.get("status") == "downloaded":
                    chroma_client = ChromaDBClient(
                        persist_directory=os.path.join(chromadb_temp_dir, "words"),
                        mode="read",
                    )
                    logger.info("✅ ChromaDB client initialized")
                else:
                    logger.warning("ChromaDB snapshot not found or download failed")
                    collected_metrics["ChromaDBDownloadFailed"] = 1
            except Exception as e:
                logger.warning("Failed to initialize ChromaDB client: %s", e)
                collected_metrics["ChromaDBDownloadFailed"] = 1
        else:
            collected_metrics["ChromaDBValidationSkipped"] = 1

        # Validate labels using appropriate approach:
        # - INVALID labels: CoVe-only (like dev script)
        # - PENDING labels: Three-tier (rule-based → ChromaDB → CoVe)
        labels_validated_rules = 0
        labels_invalidated_rules = 0
        labels_validated_chromadb = 0
        labels_invalidated_chromadb = 0
        labels_validated_cove = 0
        labels_invalidated_cove = 0
        rule_validation_duration_ms = 0

        if use_cove_only:
            logger.info("Validating %d INVALID CORE_LABELS using CoVe-only approach...", len(pending_labels))
            # Skip rule-based and ChromaDB for INVALID labels
            still_pending_after_rules = pending_labels
            validated_by_rules = []
            invalidated_by_rules = []
            still_pending_after_chromadb = pending_labels
            validated_labels_chromadb = []
            invalidated_labels_chromadb = []
            chromadb_validation_duration_ms = 0
            needs_review_labels = []
        else:
            logger.info("Validating %d existing PENDING labels using three-tier approach...", len(pending_labels))

            # Import validation functions
            from receipt_label.langchain.nodes.validate_labels_chromadb import validate_labels_chromadb
            from receipt_label.langchain.state.currency_validation import CurrencyAnalysisState
            from receipt_label.label_validation.validate_date import _is_date
            from receipt_label.label_validation.validate_time import _is_time
            from receipt_label.label_validation.validate_phone_number import _is_phone_number

            # Step 0: Rule-based validation for structured formats (DATE, TIME, PHONE_NUMBER)
            # Only for PENDING labels (INVALID labels skip this step)
            # This catches obvious mislabelings quickly and cheaply
            still_pending_after_rules = []
            validated_by_rules = []
            invalidated_by_rules = []
            labels_validated_rules = 0
            labels_invalidated_rules = 0
            needs_review_labels = []  # Initialize here for scope

            logger.info("Step 0: Rule-based validation for DATE, TIME, PHONE_NUMBER...")
            rule_validation_start = time.time()

            # Create word text lookup
            word_text_lookup = {
                (word.line_id, word.word_id): word.text
                for word in words
            }

            for label in pending_labels:
                word_text = word_text_lookup.get((label.line_id, label.word_id), "")
                label_type = str(label.label).upper()

                if label_type == "DATE":
                    if _is_date(word_text):
                        label.validation_status = ValidationStatus.VALID.value
                        validated_by_rules.append(label)
                        labels_validated_rules += 1
                        logger.debug("  ✅ DATE validated by pattern: '%s'", word_text)
                    else:
                        label.validation_status = ValidationStatus.INVALID.value
                        invalidated_by_rules.append(label)
                        labels_invalidated_rules += 1
                        logger.debug("  ❌ DATE invalidated by pattern: '%s'", word_text)
                elif label_type == "TIME":
                    if _is_time(word_text):
                        label.validation_status = ValidationStatus.VALID.value
                        validated_by_rules.append(label)
                        labels_validated_rules += 1
                        logger.debug("  ✅ TIME validated by pattern: '%s'", word_text)
                    else:
                        label.validation_status = ValidationStatus.INVALID.value
                        invalidated_by_rules.append(label)
                        labels_invalidated_rules += 1
                        logger.debug("  ❌ TIME invalidated by pattern: '%s'", word_text)
                elif label_type == "PHONE_NUMBER":
                    if _is_phone_number(word_text):
                        label.validation_status = ValidationStatus.VALID.value
                        validated_by_rules.append(label)
                        labels_validated_rules += 1
                        logger.debug("  ✅ PHONE_NUMBER validated by pattern: '%s'", word_text)
                    else:
                        label.validation_status = ValidationStatus.INVALID.value
                        invalidated_by_rules.append(label)
                        labels_invalidated_rules += 1
                        logger.debug("  ❌ PHONE_NUMBER invalidated by pattern: '%s'", word_text)
                else:
                    # Not a rule-based label type - keep for ChromaDB validation
                    still_pending_after_rules.append(label)

            rule_validation_duration_ms = (time.time() - rule_validation_start) * 1000
            logger.info(
                "Rule-based validation results: %d VALID, %d INVALID, %d still PENDING",
                labels_validated_rules,
                labels_invalidated_rules,
                len(still_pending_after_rules),
            )

            # Step 1: Try ChromaDB validation for remaining labels (fast, cheap)
            still_pending_after_chromadb = []
            chromadb_validation_duration_ms = 0

            if chroma_client and still_pending_after_rules:
                logger.info("Step 1: Validating %d remaining labels with ChromaDB similarity search...", len(still_pending_after_rules))
                chromadb_validation_start = time.time()

                # Create a minimal state with remaining PENDING labels (after rule-based validation)
                validation_state = CurrencyAnalysisState(
                    image_id=image_id,
                    receipt_id=str(receipt_id),
                    receipt_word_labels_to_update=still_pending_after_rules.copy(),
                    receipt_word_labels_to_add=[],
                    receipt_metadata=metadata,
                    lines=lines,
                    words=words,
                )

                # Run ChromaDB validation
                validation_result = await validate_labels_chromadb(
                    state=validation_state,
                    chroma_client=chroma_client,
                    line_context_range=2,
                    similarity_threshold=0.75,
                    min_matches=3,
                    conflict_threshold=0.65,
                )

                chromadb_validation_duration_ms = (time.time() - chromadb_validation_start) * 1000

                # Count ChromaDB validation results
                validated_labels_chromadb = []
                invalidated_labels_chromadb = []
                labels_validated_chromadb = 0
                labels_invalidated_chromadb = 0
                # still_pending_after_chromadb already initialized above

                for label in still_pending_after_rules:
                    status = str(getattr(label, "validation_status", "") or "").upper()
                    if status == "VALID":
                        validated_labels_chromadb.append(label)
                        labels_validated_chromadb += 1
                    elif status == "INVALID":
                        invalidated_labels_chromadb.append(label)
                        labels_invalidated_chromadb += 1
                    else:
                        # Still PENDING - will try CoVe fallback
                        still_pending_after_chromadb.append(label)

                logger.info(
                    "ChromaDB results: %d VALID, %d INVALID, %d still PENDING",
                    labels_validated_chromadb,
                    labels_invalidated_chromadb,
                    len(still_pending_after_chromadb),
                )
            else:
                if not chroma_client:
                    logger.warning("ChromaDB client not available - skipping ChromaDB validation")
                if not still_pending_after_rules:
                    logger.info("No labels remaining after rule-based validation - skipping ChromaDB")
                still_pending_after_chromadb = still_pending_after_rules.copy()
                validated_labels_chromadb = []
                invalidated_labels_chromadb = []
                labels_validated_chromadb = 0
                labels_invalidated_chromadb = 0

        # Step 2: CoVe validation for labels that ChromaDB couldn't validate (or all labels for INVALID)
        # This step runs for both INVALID and PENDING labels
        labels_validated_cove = 0
        labels_invalidated_cove = 0
        cove_validation_duration_ms = 0
        validated_labels_cove = []
        invalidated_labels_cove = []

        if still_pending_after_chromadb:
            if use_cove_only:
                logger.info("Validating %d INVALID CORE_LABELS with CoVe...", len(still_pending_after_chromadb))
            else:
                logger.info("Step 2: Validating %d labels with CoVe (ChromaDB couldn't validate)...", len(still_pending_after_chromadb))
            cove_validation_start = time.time()

            try:
                # Import CoVe validation function
                from receipt_label.langchain.validate_pending_labels_cove import validate_pending_labels_cove_simple

                # Build receipt text for context
                receipt_text = "\n".join([f"Line {line.line_id}: {line.text}" for line in lines])

                # Run CoVe validation
                updated_labels = await validate_pending_labels_cove_simple(
                    pending_labels=still_pending_after_chromadb,
                    lines=lines,
                    words=words,
                    receipt_text=receipt_text,
                    ollama_api_key=os.environ["OLLAMA_API_KEY"],
                    langsmith_api_key=os.environ.get("LANGCHAIN_API_KEY"),
                )

                cove_validation_duration_ms = (time.time() - cove_validation_start) * 1000

                # Count CoVe validation results
                # For INVALID labels, only allow VALID or INVALID (no NEEDS_REVIEW)
                # For PENDING labels, allow VALID, INVALID, or NEEDS_REVIEW
                for label in updated_labels:
                    status = str(getattr(label, "validation_status", "") or "").upper()
                    if status == "VALID":
                        validated_labels_cove.append(label)
                        labels_validated_cove += 1
                    elif status == "INVALID":
                        invalidated_labels_cove.append(label)
                        labels_invalidated_cove += 1
                    else:
                        # Still PENDING or NEEDS_REVIEW
                        if target_status == ValidationStatus.INVALID:
                            # For INVALID labels, mark as INVALID (like dev script)
                            label.validation_status = ValidationStatus.INVALID.value
                            invalidated_labels_cove.append(label)
                            labels_invalidated_cove += 1
                        else:
                            # For PENDING labels, allow NEEDS_REVIEW
                            label.validation_status = ValidationStatus.NEEDS_REVIEW.value
                            needs_review_labels.append(label)

                logger.info(
                    "CoVe validation results: %d VALID, %d INVALID, %d NEEDS_REVIEW",
                    labels_validated_cove,
                    labels_invalidated_cove,
                    len(needs_review_labels),
                )
            except Exception as e:
                logger.warning("CoVe validation failed: %s", e)
                # Mark all remaining labels based on target status
                if use_cove_only:
                    # For INVALID labels, mark as INVALID if CoVe fails (like dev script)
                    for pending_label in still_pending_after_chromadb:
                        pending_label.validation_status = ValidationStatus.INVALID.value
                        invalidated_labels_cove.append(pending_label)
                        labels_invalidated_cove += 1
                else:
                    # For PENDING labels, mark as NEEDS_REVIEW if CoVe fails
                    for pending_label in still_pending_after_chromadb:
                        pending_label.validation_status = ValidationStatus.NEEDS_REVIEW.value
                        needs_review_labels.append(pending_label)

        # Update DynamoDB with all validated/invalidated/needs_review labels
        all_validated = validated_by_rules + validated_labels_chromadb + validated_labels_cove
        all_invalidated = invalidated_by_rules + invalidated_labels_chromadb + invalidated_labels_cove
        labels_to_update = all_validated + all_invalidated + needs_review_labels

        if labels_to_update:
            logger.info(
                "Updating %d labels in DynamoDB: %d VALID (Rules: %d, ChromaDB: %d, CoVe: %d), %d INVALID (Rules: %d, ChromaDB: %d, CoVe: %d), %d NEEDS_REVIEW",
                len(labels_to_update),
                len(all_validated),
                labels_validated_rules,
                labels_validated_chromadb,
                labels_validated_cove,
                len(all_invalidated),
                labels_invalidated_rules,
                labels_invalidated_chromadb,
                labels_invalidated_cove,
                len(needs_review_labels),
            )
            dynamo.update_receipt_word_labels(labels_to_update)

        total_validated = labels_validated_rules + labels_validated_chromadb + labels_validated_cove
        total_invalidated = labels_invalidated_rules + labels_invalidated_chromadb + labels_invalidated_cove
        total_duration_ms = (time.time() - start_time) * 1000

        # Calculate efficiency metrics
        total_processed = len(pending_labels)
        total_validated_or_invalidated = total_validated + total_invalidated
        validation_success_rate = (
            (total_validated_or_invalidated / total_processed * 100)
            if total_processed > 0
            else 0
        )
        chromadb_validation_rate = (
            ((labels_validated_chromadb + labels_invalidated_chromadb) / total_validated_or_invalidated * 100)
            if total_validated_or_invalidated > 0
            else 0
        )

        # Collect all metrics
        collected_metrics.update({
            "LabelsValidatedRules": labels_validated_rules,
            "LabelsInvalidatedRules": labels_invalidated_rules,
            "LabelsValidatedChromaDB": labels_validated_chromadb,
            "LabelsInvalidatedChromaDB": labels_invalidated_chromadb,
            "LabelsValidatedCoVe": labels_validated_cove,
            "LabelsInvalidatedCoVe": labels_invalidated_cove,
            "LabelsNeedsReview": len(needs_review_labels),
            "TotalLabelsProcessed": total_processed,
            "ValidateReceiptDuration": total_duration_ms,
            "RuleValidationDuration": rule_validation_duration_ms,
            "ChromaDBDownloadDuration": chromadb_download_duration_ms,
            "ChromaDBValidationDuration": chromadb_validation_duration_ms,
            "CoVeValidationDuration": cove_validation_duration_ms,
            "ValidationSuccessRate": validation_success_rate,
            "ChromaDBValidationRate": chromadb_validation_rate,
        })

        # Track cost efficiency
        collected_metrics["ChromaDBOnlyValidation"] = 1

        # Properties for detailed analysis
        properties = {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "pending_labels_count": total_processed,
            "chromadb_validated": labels_validated_chromadb,
            "chromadb_invalidated": labels_invalidated_chromadb,
            "cove_validated": labels_validated_cove,
            "cove_invalidated": labels_invalidated_cove,
            "needs_review": len(needs_review_labels),
            "chromadb_download_time_ms": chromadb_download_duration_ms,
            "chromadb_validation_time_ms": chromadb_validation_duration_ms,
            "cove_validation_time_ms": cove_validation_duration_ms,
            "rule_validation_time_ms": rule_validation_duration_ms,
            "total_duration_ms": total_duration_ms,
            "validation_success_rate": validation_success_rate,
            "chromadb_validation_rate": chromadb_validation_rate,
        }

        # Log all metrics via EMF (single log line, no API calls)
        emf_metrics.log_metrics(
            collected_metrics,
            dimensions=None,
            properties=properties,
        )

        logger.info(
            "✅ Validation complete: %d VALID (Rules: %d, ChromaDB: %d, CoVe: %d), %d INVALID (Rules: %d, ChromaDB: %d, CoVe: %d), %d NEEDS_REVIEW",
            total_validated,
            labels_validated_rules,
            labels_validated_chromadb,
            labels_validated_cove,
            total_invalidated,
            labels_invalidated_rules,
            labels_invalidated_chromadb,
            labels_invalidated_cove,
            len(needs_review_labels),
        )

        return {
            "success": True,
            "index": index,
            "image_id": image_id,
            "receipt_id": receipt_id,
            "labels_validated_rules": labels_validated_rules,
            "labels_invalidated_rules": labels_invalidated_rules,
            "labels_validated_chromadb": labels_validated_chromadb,
            "labels_invalidated_chromadb": labels_invalidated_chromadb,
            "labels_validated_cove": labels_validated_cove,
            "labels_invalidated_cove": labels_invalidated_cove,
            "labels_needs_review": len(needs_review_labels),
            "total_pending": len(pending_labels),
        }

    except Exception as e:
        logger.error("Error validating receipt: %s", e, exc_info=True)

        # Log error via EMF
        error_type = type(e).__name__
        emf_metrics.log_metrics(
            {"ValidateReceiptError": 1},
            dimensions={"error_type": error_type},
            properties={
                "error": str(e),
                "image_id": image_id if "image_id" in locals() else "unknown",
                "receipt_id": receipt_id if "receipt_id" in locals() else "unknown",
                "index": index if "index" in locals() else -1,
            },
        )

        return {
            "success": False,
            "index": index if "index" in locals() else -1,
            "error": str(e),
        }

    finally:
        # Cleanup ChromaDB temp directory
        if chromadb_temp_dir and os.path.exists(chromadb_temp_dir):
            try:
                shutil.rmtree(chromadb_temp_dir, ignore_errors=True)
            except Exception as e:
                logger.warning("Failed to cleanup ChromaDB temp directory: %s", e)




def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Synchronous wrapper for async handler."""
    return asyncio.run(handler(event, context))

