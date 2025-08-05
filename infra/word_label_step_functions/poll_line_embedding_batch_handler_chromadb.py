"""
Delta-based polling handler for ChromaDB architecture.

This handler creates delta files in S3 instead of directly writing to ChromaDB.
The compaction job will later merge these deltas into the main ChromaDB collection.
"""

import json
import boto3
import uuid
import gzip
import os
from datetime import datetime, timezone
from logging import INFO, Formatter, StreamHandler, getLogger

from receipt_label.poll_line_embedding_batch.poll_line_batch import (
    download_openai_batch_result,
    get_openai_batch_status,
    get_receipt_descriptions,
    mark_batch_complete,
    update_line_embedding_status_to_success,
    write_line_embedding_results_to_dynamo,
    _parse_metadata_from_line_id,
    _parse_prev_next_from_formatted,
)
from receipt_label.submit_line_embedding_batch.submit_line_batch import (
    _format_line_context_embedding_input,
)

logger = getLogger()
logger.setLevel(INFO)

if len(logger.handlers) == 0:
    handler = StreamHandler()
    handler.setFormatter(
        Formatter(
            "[%(levelname)s] %(asctime)s.%(msecs)dZ %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(handler)

# S3 client for delta storage
s3_client = boto3.client("s3")
DELTA_BUCKET = os.environ.get("DELTA_BUCKET", "chromadb-deltas-bucket")


def save_embeddings_delta(results, descriptions, batch_id):
    """
    Save embeddings as a delta file to S3.
    
    This creates a compressed JSON file with ChromaDB-compatible data structure
    that will be processed later by the compaction job.
    """
    # Prepare ChromaDB-compatible data
    delta_data = {
        "batch_id": batch_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ids": [],
        "embeddings": [],
        "metadatas": [],
        "documents": [],
    }
    
    for result in results:
        # Parse metadata from custom_id
        meta = _parse_metadata_from_line_id(result["custom_id"])
        image_id = meta["image_id"]
        receipt_id = meta["receipt_id"]
        line_id = meta["line_id"]
        
        # Get receipt details
        receipt_details = descriptions[image_id][receipt_id]
        lines = receipt_details["lines"]
        target_line = next((l for l in lines if l.line_id == line_id), None)
        
        if not target_line:
            logger.warning(
                f"No ReceiptLine found for image_id={image_id}, "
                f"receipt_id={receipt_id}, line_id={line_id}"
            )
            continue
            
        # Calculate line metrics
        line_words = [
            w for w in receipt_details["words"] if w.line_id == line_id
        ]
        avg_confidence = (
            sum(w.confidence for w in line_words) / len(line_words)
            if line_words
            else target_line.confidence
        )
        word_count = len(line_words)
        
        # Get line centroid and dimensions
        x_center, y_center = target_line.calculate_centroid()
        width = target_line.bounding_box["width"]
        height = target_line.bounding_box["height"]
        
        # Format the line context to extract prev/next lines
        embedding_input = _format_line_context_embedding_input(
            target_line, lines
        )
        prev_line, next_line = _parse_prev_next_from_formatted(embedding_input)
        
        # Merchant name handling
        metadata = receipt_details["metadata"]
        if (
            hasattr(metadata, "canonical_merchant_name")
            and metadata.canonical_merchant_name
        ):
            merchant_name = metadata.canonical_merchant_name
        else:
            merchant_name = metadata.merchant_name
            
        # Standardize merchant name format
        if merchant_name:
            merchant_name = merchant_name.strip().title()
        
        # Add to delta
        delta_data["ids"].append(result["custom_id"])
        delta_data["embeddings"].append(result["embedding"])
        delta_data["metadatas"].append(
            {
                "image_id": image_id,
                "receipt_id": receipt_id,
                "line_id": line_id,
                "source": meta["source"],
                "text": target_line.text,
                "x": x_center,
                "y": y_center,
                "width": width,
                "height": height,
                "confidence": target_line.confidence,
                "avg_word_confidence": avg_confidence,
                "word_count": word_count,
                "prev_line": prev_line,
                "next_line": next_line,
                "merchant_name": merchant_name,
                "angle_degrees": target_line.angle_degrees,
            }
        )
        delta_data["documents"].append(target_line.text)
    
    # Compress and save to S3
    delta_id = str(uuid.uuid4())
    delta_key = f"deltas/{delta_id}/embeddings.json.gz"
    
    compressed = gzip.compress(json.dumps(delta_data).encode("utf-8"))
    s3_client.put_object(
        Bucket=DELTA_BUCKET,
        Key=delta_key,
        Body=compressed,
        ContentType="application/gzip",
        Metadata={
            "batch_id": batch_id,
            "count": str(len(delta_data["ids"])),
        },
    )
    
    return {
        "delta_id": delta_id,
        "delta_key": delta_key,
        "embedding_count": len(delta_data["ids"]),
    }


def poll_handler(event, context):
    """
    Poll an embedding batch and save results as deltas to S3.
    
    This handler creates delta files that will be processed by the
    compaction job, avoiding direct writes to ChromaDB.

    Args:
        event: The event object from the step function.
        context: The context object from the step function.

    Returns:
        A dictionary containing the status code and delta information.
    """
    logger.info(
        "Starting poll_line_embedding_batch_handler (Delta pattern)"
    )
    logger.info(f"Event: {event}")

    batch_id = event["batch_id"]
    openai_batch_id = event["openai_batch_id"]

    # Check the batch status
    batch_status = get_openai_batch_status(openai_batch_id)
    logger.info(f"Batch {openai_batch_id} status: {batch_status}")

    if batch_status == "completed":
        # Download the batch results
        results = download_openai_batch_result(openai_batch_id)
        logger.info(f"Downloaded {len(results)} embedding results")

        # Get receipt details for all involved receipts
        descriptions = get_receipt_descriptions(results)
        logger.info(f"Retrieved details for {len(descriptions)} receipts")

        # Save delta instead of direct ChromaDB upsert
        delta_result = save_embeddings_delta(results, descriptions, batch_id)
        logger.info(
            f"Saved delta {delta_result['delta_id']} with "
            f"{delta_result['embedding_count']} embeddings"
        )

        # Write results to DynamoDB for tracking
        written = write_line_embedding_results_to_dynamo(
            results, descriptions, batch_id
        )
        logger.info(f"Wrote {written} embedding results to DynamoDB")

        # Update the embedding status of the lines
        update_line_embedding_status_to_success(results, descriptions)
        logger.info("Updated line embedding status to SUCCESS")

        # Mark the batch as complete
        mark_batch_complete(batch_id)
        logger.info(f"Marked batch {batch_id} as complete")

        return {
            "statusCode": 200,
            "batch_id": batch_id,
            "openai_batch_id": openai_batch_id,
            "batch_status": batch_status,
            "results_count": len(results),
            "delta_id": delta_result["delta_id"],
            "delta_key": delta_result["delta_key"],
            "embedding_count": delta_result["embedding_count"],
            "storage": "s3_delta",  # Indicate we're using delta pattern
        }

    return {
        "statusCode": 200,
        "batch_id": batch_id,
        "openai_batch_id": openai_batch_id,
        "batch_status": batch_status,
    }
