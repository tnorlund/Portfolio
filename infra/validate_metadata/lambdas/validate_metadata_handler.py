"""Container Lambda handler for validating ReceiptMetadata for a single receipt."""

import asyncio
import json
import logging
import os
import tempfile
import time
from typing import Any, Dict

import boto3
from receipt_dynamo import DynamoClient
from receipt_label.langchain.currency_validation import analyze_receipt_simple

# Import EMF metrics utility
# Utils directory is in the same directory as the handler (lambdas/utils/)
from utils.emf_metrics import emf_metrics

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Validate ReceiptMetadata for a single receipt using LangGraph + CoVe.

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
            "is_valid": False,
            "metadata_updated": True,
            "google_places_updated": True
        }
    """
    s3_client = boto3.client("s3")
    dynamo = DynamoClient(os.environ["DYNAMODB_TABLE_NAME"])

    index = event["index"]
    manifest_s3_key = event["manifest_s3_key"]
    manifest_s3_bucket = event["manifest_s3_bucket"]

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

        if not metadata:
            logger.warning("No ReceiptMetadata found for %s/%d", image_id, receipt_id)
            return {
                "success": False,
                "index": index,
                "image_id": image_id,
                "receipt_id": receipt_id,
                "error": "No ReceiptMetadata found",
            }

        logger.info(
            "Fetched receipt data: %d lines, %d words, metadata=%s",
            len(lines),
            len(words),
            metadata.merchant_name,
        )

        # Get API keys from environment
        ollama_api_key = os.environ.get("OLLAMA_API_KEY")
        langsmith_api_key = os.environ.get("LANGCHAIN_API_KEY")
        google_places_api_key = os.environ.get("GOOGLE_PLACES_API_KEY")

        if not ollama_api_key:
            raise ValueError("OLLAMA_API_KEY is required")
        if not langsmith_api_key:
            logger.warning("LANGCHAIN_API_KEY not found - tracing will be disabled")

        # Run LangGraph analysis with metadata validation enabled
        logger.info("Running metadata validation with LangGraph + CoVe...")
        analysis = await analyze_receipt_simple(
            client=dynamo,
            image_id=image_id,
            receipt_id=receipt_id,
            ollama_api_key=ollama_api_key,
            langsmith_api_key=langsmith_api_key,
            save_labels=False,  # Don't save labels during validation
            dry_run=False,  # Always update metadata if invalid
            save_dev_state=False,
            receipt_lines=lines,
            receipt_words=words,
            receipt_metadata=metadata,
            google_places_api_key=google_places_api_key,
            update_metadata=True,  # Enable metadata updates
        )

        # Extract validation results
        validation_results = analysis.metadata_validation or {}
        is_valid = validation_results.get("is_valid", True)
        reasoning = validation_results.get("reasoning", "No validation performed")
        google_places_updated = validation_results.get("google_places_updated", False)

        # Check if metadata was actually updated
        # The analyze_receipt_simple function updates metadata in DynamoDB if:
        # 1. Validation found issues (is_valid=False)
        # 2. Google Places API found a better match (google_places_updated=True)
        metadata_updated = not is_valid and google_places_updated

        # Calculate duration
        duration_ms = (time.time() - start_time) * 1000

        # Collect metrics
        collected_metrics.update({
            "MetadataValidated": 1,
            "ValidationDuration": duration_ms,
        })

        if metadata_updated:
            collected_metrics["MetadataUpdated"] = 1
        if google_places_updated:
            collected_metrics["GooglePlacesSearches"] = 1

        # Properties for detailed analysis
        properties = {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "merchant_name": metadata.merchant_name,
            "is_valid": is_valid,
            "metadata_updated": metadata_updated,
        }

        # Log all metrics via EMF
        emf_metrics.log_metrics(
            collected_metrics,
            dimensions=None,
            properties=properties,
        )

        logger.info(
            "Validation complete: is_valid=%s, metadata_updated=%s",
            is_valid,
            metadata_updated,
        )

        return {
            "success": True,
            "index": index,
            "image_id": image_id,
            "receipt_id": receipt_id,
            "is_valid": is_valid,
            "metadata_updated": metadata_updated,
            "google_places_updated": google_places_updated,
            "reasoning": reasoning,
            "duration_ms": duration_ms,
        }

    except Exception as e:
        # Log error via EMF
        error_type = type(e).__name__
        duration_ms = (time.time() - start_time) * 1000

        emf_metrics.log_metrics(
            {"ValidationError": 1, "ValidationDuration": duration_ms},
            dimensions={"error_type": error_type},
            properties={
                "error": str(e),
                "index": index,
            },
        )

        logger.error("Error validating metadata: %s", e, exc_info=True)
        return {
            "success": False,
            "index": index,
            "error": str(e),
            "error_type": error_type,
        }


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """Synchronous wrapper for async handler."""
    return asyncio.run(handler(event, context))

