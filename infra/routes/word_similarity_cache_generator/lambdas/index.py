"""Lambda handler for generating word similarity cache.

Queries ChromaDB for words similar to "milk" and stores results with
receipt context for visualization.
"""

import json
import logging
import os
import random
import shutil
import tempfile
from datetime import datetime, timezone

import boto3
from receipt_chroma.data.chroma_client import ChromaClient
from receipt_chroma.s3 import download_snapshot_atomic

from receipt_dynamo import DynamoClient

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment variables
DYNAMODB_TABLE_NAME = os.environ["DYNAMODB_TABLE_NAME"]
CHROMADB_BUCKET = os.environ["CHROMADB_BUCKET"]
S3_CACHE_BUCKET = os.environ.get("S3_CACHE_BUCKET", CHROMADB_BUCKET)
CACHE_KEY = "word-similarity-cache/milk.json"
TARGET_WORD = "milk"

# Initialize clients
s3_client = boto3.client("s3")


def calculate_bounding_box_for_word_context(target_word, context_lines):
    """Calculate a bounding box for word context (target line + neighbors).

    Args:
        target_word: The target ReceiptWord object
        context_lines: List of ReceiptLine objects around the target word

    Returns:
        dict: Bounding box with keys 'tl', 'tr', 'bl', 'br', each containing
              {'x': float, 'y': float}, or None if no valid data
    """
    if not context_lines:
        # Fallback to word's own coordinates with padding
        if not target_word:
            return None
        padding = 0.02
        return {
            "tl": {
                "x": max(0, target_word.top_left["x"] - padding),
                "y": min(1, target_word.top_left["y"] + padding),
            },
            "tr": {
                "x": min(1, target_word.top_right["x"] + padding),
                "y": min(1, target_word.top_right["y"] + padding),
            },
            "bl": {
                "x": max(0, target_word.bottom_left["x"] - padding),
                "y": max(0, target_word.bottom_left["y"] - padding),
            },
            "br": {
                "x": min(1, target_word.bottom_right["x"] + padding),
                "y": max(0, target_word.bottom_right["y"] - padding),
            },
        }

    # Collect all corner points from context lines
    all_x = []
    all_y = []

    for line in context_lines:
        all_x.extend(
            [
                line.top_left["x"],
                line.top_right["x"],
                line.bottom_left["x"],
                line.bottom_right["x"],
            ]
        )
        all_y.extend(
            [
                line.top_left["y"],
                line.top_right["y"],
                line.bottom_left["y"],
                line.bottom_right["y"],
            ]
        )

    # Find bounds with small padding
    padding = 0.01
    min_x = max(0, min(all_x) - padding)
    max_x = min(1, max(all_x) + padding)
    min_y = max(0, min(all_y) - padding)
    max_y = min(1, max(all_y) + padding)

    # Create bounding box corners
    # Note: In OCR coordinates, y=0 is at bottom, so:
    # - top_left has highest y value
    # - bottom_left has lowest y value
    return {
        "tl": {"x": min_x, "y": max_y},
        "tr": {"x": max_x, "y": max_y},
        "bl": {"x": min_x, "y": min_y},
        "br": {"x": max_x, "y": min_y},
    }


def handler(_event, _context):
    """Handle EventBridge scheduled event to generate word similarity cache.

    Args:
        _event: EventBridge event (unused)
        _context: Lambda context (unused)

    Returns:
        dict: Status of cache generation
    """
    logger.info("Starting word similarity cache generation for '%s'", TARGET_WORD)

    # Create temporary directory for ChromaDB snapshot
    temp_dir = tempfile.mkdtemp()
    chroma_client = None

    try:
        # Initialize DynamoDB client
        dynamo_client = DynamoClient(DYNAMODB_TABLE_NAME)

        # Download ChromaDB words snapshot from S3
        logger.info(
            "Downloading ChromaDB words snapshot from S3: %s/words", CHROMADB_BUCKET
        )
        download_result = download_snapshot_atomic(
            bucket=CHROMADB_BUCKET,
            collection="words",  # Use words collection, not lines
            local_path=temp_dir,
            verify_integrity=True,
        )

        if download_result.get("status") != "downloaded":
            logger.error("Failed to download snapshot: %s", download_result)
            return {
                "statusCode": 500,
                "body": json.dumps(
                    {"error": "Failed to download ChromaDB words snapshot"}
                ),
            }

        logger.info(
            "ChromaDB words snapshot downloaded: version_id=%s, local_path=%s",
            download_result.get("version_id"),
            temp_dir,
        )

        # Initialize ChromaDB client in read mode
        chroma_client = ChromaClient(
            persist_directory=temp_dir,
            mode="read",
        )

        # Verify that the "words" collection exists
        available_collections = chroma_client.list_collections()
        if "words" not in available_collections:
            logger.error(
                "Collection 'words' not found in snapshot. Available: %s",
                available_collections,
            )
            return {
                "statusCode": 500,
                "body": json.dumps(
                    {"error": f"Collection 'words' not found. Available: {available_collections}"}
                ),
            }

        logger.info("Collection 'words' found. Total collections: %d", len(available_collections))

        # Step 1: Find words matching "milk" (try lowercase first, then uppercase)
        logger.info("Searching for '%s' words in ChromaDB", TARGET_WORD)

        # Get collection for direct querying
        words_collection = chroma_client.get_collection("words")

        # Try exact match for "milk"
        milk_results = words_collection.get(
            where={"text": TARGET_WORD},
            include=["embeddings", "metadatas"],
            limit=50,
        )

        # If no results, try uppercase
        if not milk_results["ids"]:
            logger.info("No lowercase '%s' found, trying uppercase", TARGET_WORD)
            milk_results = words_collection.get(
                where={"text": TARGET_WORD.upper()},
                include=["embeddings", "metadatas"],
                limit=50,
            )

        # If still no results, try title case
        if not milk_results["ids"]:
            logger.info("No uppercase found, trying title case")
            milk_results = words_collection.get(
                where={"text": TARGET_WORD.title()},
                include=["embeddings", "metadatas"],
                limit=50,
            )

        if not milk_results["ids"]:
            logger.warning("No '%s' words found in ChromaDB", TARGET_WORD)
            return {
                "statusCode": 200,
                "body": json.dumps({"message": f"No '{TARGET_WORD}' words found in ChromaDB"}),
            }

        logger.info("Found %d '%s' words in ChromaDB", len(milk_results["ids"]), TARGET_WORD)

        # Step 2: Select one randomly
        selected_idx = random.randint(0, len(milk_results["ids"]) - 1)
        selected_metadata = milk_results["metadatas"][selected_idx]

        # Handle embeddings (may be NumPy arrays)
        embeddings_raw = milk_results["embeddings"]
        if embeddings_raw is None or len(embeddings_raw) == 0:
            logger.error("No embeddings found for selected word")
            return {
                "statusCode": 500,
                "body": json.dumps({"error": "No embedding found for selected word"}),
            }

        # Convert to list if needed
        query_embedding = embeddings_raw[selected_idx]
        if hasattr(query_embedding, "tolist"):
            query_embedding = query_embedding.tolist()

        logger.info(
            "Selected seed word: image_id=%s, receipt_id=%s, line_id=%s, word_id=%s",
            selected_metadata.get("image_id"),
            selected_metadata.get("receipt_id"),
            selected_metadata.get("line_id"),
            selected_metadata.get("word_id"),
        )

        # Step 3: Get original receipt context
        logger.info("Loading original receipt context")
        original_image_id = selected_metadata.get("image_id")
        original_receipt_id = int(selected_metadata.get("receipt_id", 0))
        original_line_id = int(selected_metadata.get("line_id", 0))
        original_word_id = int(selected_metadata.get("word_id", 0))

        original_receipt = dynamo_client.get_receipt_details(
            original_image_id,
            original_receipt_id,
        )

        # Find the target word
        original_target_word = next(
            (w for w in original_receipt.words
             if w.line_id == original_line_id and w.word_id == original_word_id),
            None
        )

        # Get context lines (target line and adjacent lines)
        context_line_ids = {original_line_id - 1, original_line_id, original_line_id + 1}
        original_context_lines = [
            line for line in original_receipt.lines
            if line.line_id in context_line_ids
        ]
        original_context_words = [
            word for word in original_receipt.words
            if word.line_id in context_line_ids
        ]
        original_context_labels = [
            label for label in original_receipt.labels
            if label.line_id in context_line_ids
        ]

        # Calculate bounding box for original
        original_bbox = calculate_bounding_box_for_word_context(
            original_target_word, original_context_lines
        )

        logger.info(
            "Original context: %d lines, %d words, %d labels",
            len(original_context_lines),
            len(original_context_words),
            len(original_context_labels),
        )

        # Step 4: Query ChromaDB for similar words
        logger.info("Querying ChromaDB for similar words")
        similar_results = chroma_client.query(
            collection_name="words",
            query_embeddings=[query_embedding],
            n_results=12,  # Get extra to filter out self and ensure we have 8
            include=["metadatas", "distances", "documents"],
        )

        similar_metadatas = similar_results.get("metadatas", [[]])[0]
        similar_distances = similar_results.get("distances", [[]])[0]
        similar_documents = similar_results.get("documents", [[]])[0]

        # Step 5: Process similar words and get receipt context
        similar_words_data = []
        seen_receipts = set()

        for metadata, distance, document in zip(
            similar_metadatas,
            similar_distances,
            similar_documents,
            strict=True,
        ):
            if not metadata:
                continue

            image_id = metadata.get("image_id")
            receipt_id_str = metadata.get("receipt_id")

            if not image_id or not receipt_id_str:
                continue

            try:
                receipt_id = int(receipt_id_str)
            except (ValueError, TypeError):
                continue

            # Skip self (same receipt)
            if image_id == original_image_id and receipt_id == original_receipt_id:
                continue

            # Skip duplicates (same receipt already processed)
            receipt_key = (image_id, receipt_id)
            if receipt_key in seen_receipts:
                continue
            seen_receipts.add(receipt_key)

            try:
                # Get receipt details
                similar_receipt = dynamo_client.get_receipt_details(image_id, receipt_id)

                # Find target word
                similar_line_id = int(metadata.get("line_id", 0))
                similar_word_id = int(metadata.get("word_id", 0))

                similar_target_word = next(
                    (w for w in similar_receipt.words
                     if w.line_id == similar_line_id and w.word_id == similar_word_id),
                    None
                )

                if not similar_target_word:
                    logger.warning(
                        "Target word not found in receipt: image=%s, receipt=%s, line=%s, word=%s",
                        image_id, receipt_id, similar_line_id, similar_word_id
                    )
                    continue

                # Get context lines (target line + adjacent)
                similar_context_line_ids = {
                    similar_line_id - 1, similar_line_id, similar_line_id + 1
                }
                similar_context_lines = [
                    line for line in similar_receipt.lines
                    if line.line_id in similar_context_line_ids
                ]
                similar_context_words = [
                    word for word in similar_receipt.words
                    if word.line_id in similar_context_line_ids
                ]
                similar_context_labels = [
                    label for label in similar_receipt.labels
                    if label.line_id in similar_context_line_ids
                ]

                # Calculate bounding box
                similar_bbox = calculate_bounding_box_for_word_context(
                    similar_target_word, similar_context_lines
                )

                similar_words_data.append({
                    "receipt": dict(similar_receipt.receipt),
                    "lines": [dict(line) for line in similar_context_lines],
                    "words": [dict(word) for word in similar_context_words],
                    "labels": [dict(label) for label in similar_context_labels],
                    "target_word": dict(similar_target_word),
                    "similarity_distance": float(distance),
                    "bbox": similar_bbox,
                })

                # Stop once we have 8 similar words
                if len(similar_words_data) >= 8:
                    break

            except Exception as e:
                logger.warning(
                    "Failed to process similar word: image=%s, receipt=%s, error=%s",
                    image_id, receipt_id, str(e)
                )
                continue

        logger.info("Processed %d similar words", len(similar_words_data))

        # Step 6: Build response structure
        response_data = {
            "query_word": TARGET_WORD,
            "original": {
                "receipt": dict(original_receipt.receipt),
                "lines": [dict(line) for line in original_context_lines],
                "words": [dict(word) for word in original_context_words],
                "labels": [dict(label) for label in original_context_labels],
                "target_word": dict(original_target_word) if original_target_word else None,
                "bbox": original_bbox,
            },
            "similar": similar_words_data,
            "cached_at": datetime.now(timezone.utc).isoformat(),
        }

        # Step 7: Upload to S3
        logger.info("Uploading cache to S3: %s/%s", S3_CACHE_BUCKET, CACHE_KEY)
        s3_client.put_object(
            Bucket=S3_CACHE_BUCKET,
            Key=CACHE_KEY,
            Body=json.dumps(response_data, default=str),
            ContentType="application/json",
        )

        logger.info(
            "Cache generation complete: query_word=%s, similar_count=%d",
            TARGET_WORD,
            len(similar_words_data),
        )

        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "Cache generated successfully",
                "query_word": TARGET_WORD,
                "similar_count": len(similar_words_data),
            }),
        }

    except Exception as e:
        logger.error("Error generating cache: %s", e, exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }
    finally:
        # Close ChromaDB client if initialized
        if chroma_client is not None:
            try:
                chroma_client.close()
                logger.info("Closed ChromaDB client")
            except Exception:
                logger.warning("Failed to close ChromaDB client")

        # Cleanup temporary directory
        try:
            shutil.rmtree(temp_dir)
            logger.info("Cleaned up temporary directory: %s", temp_dir)
        except Exception as cleanup_error:
            logger.warning("Failed to cleanup temp directory: %s", cleanup_error)
