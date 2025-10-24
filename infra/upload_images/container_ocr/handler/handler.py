"""
Container-based Lambda handler for OCR processing with integrated merchant validation.

Combines:
1. OCR parsing and storage (from process_ocr_results.py)
2. Merchant validation and embedding (from embed_from_ndjson)
"""

import json
import logging
import os
from typing import Any, Dict

from .ocr_processor import OCRProcessor
from .embedding_processor import EmbeddingProcessor

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


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
    logger.info(f"Processing {len(event.get('Records', []))} OCR records")
    
    results = []
    for record in event.get("Records", []):
        try:
            result = _process_single_record(record)
            results.append(result)
        except Exception as e:
            logger.error(f"Failed to process record: {e}", exc_info=True)
            results.append({"success": False, "error": str(e)})
    
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
    
    logger.info(f"Processing OCR for image {image_id}, job {job_id}")
    
    # Initialize OCR processor
    ocr_processor = OCRProcessor(
        table_name=os.environ["DYNAMO_TABLE_NAME"],
        raw_bucket=os.environ["RAW_BUCKET"],
        site_bucket=os.environ["SITE_BUCKET"],
        ocr_job_queue_url=os.environ["OCR_JOB_QUEUE_URL"],
        ocr_results_queue_url=os.environ["OCR_RESULTS_QUEUE_URL"],
    )
    
    # Step 1: Process OCR (parse, classify, store in DynamoDB)
    ocr_result = ocr_processor.process_ocr_job(image_id, job_id)
    
    if not ocr_result.get("success"):
        logger.error(f"OCR processing failed: {ocr_result.get('error')}")
        return ocr_result
    
    logger.info(f"OCR processing completed: {ocr_result}")
    
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
            embedding_result = embedding_processor.process_embeddings(
                image_id=image_id,
                receipt_id=receipt_id,
                lines=ocr_result.get("receipt_lines"),
                words=ocr_result.get("receipt_words"),
            )
            
            logger.info(
                f"Embeddings created for {image_type} receipt: "
                f"image_id={image_id}, receipt_id={receipt_id}, "
                f"merchant={embedding_result.get('merchant_name')}, "
                f"run_id={embedding_result.get('run_id')}"
            )
            
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
            logger.error(
                f"Merchant validation/embedding failed for {image_type}: {e}",
                exc_info=True
            )
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
    logger.info(
        f"Skipping embeddings for {image_type} (receipt_id={receipt_id}). "
        f"Will process embeddings during REFINEMENT jobs."
    )
    return ocr_result

