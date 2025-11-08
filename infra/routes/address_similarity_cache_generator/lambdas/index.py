"""Lambda handler for generating address similarity cache."""

import json
import logging
import os
import random
import shutil
import tempfile
from datetime import datetime, timezone

import boto3
from receipt_dynamo import DynamoClient
from receipt_label.utils.chroma_s3_helpers import download_snapshot_atomic
from receipt_label.vector_store.client.chromadb_client import ChromaDBClient

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment variables
DYNAMODB_TABLE_NAME = os.environ["DYNAMODB_TABLE_NAME"]
CHROMADB_BUCKET = os.environ["CHROMADB_BUCKET"]
S3_CACHE_BUCKET = os.environ.get("S3_CACHE_BUCKET", CHROMADB_BUCKET)
CACHE_KEY = "address-similarity-cache/latest.json"

# Initialize clients
s3_client = boto3.client("s3")


def calculate_bounding_box_for_lines(lines):
    """Calculate a bounding box that contains all the given lines.

    Finds the min/max x and y coordinates from all line corners and creates
    a single bounding box with corners (tl, tr, bl, br) that encompasses all lines.

    Args:
        lines: List of ReceiptLine objects

    Returns:
        dict: Bounding box with keys 'tl', 'tr', 'bl', 'br', each containing
              {'x': float, 'y': float}, or None if lines is empty
    """
    if not lines:
        return None

    # Collect all corner points from all lines
    all_x = []
    all_y = []

    for line in lines:
        # Get all corner coordinates
        all_x.extend([
            line.top_left["x"],
            line.top_right["x"],
            line.bottom_left["x"],
            line.bottom_right["x"],
        ])
        all_y.extend([
            line.top_left["y"],
            line.top_right["y"],
            line.bottom_left["y"],
            line.bottom_right["y"],
        ])

    # Find bounds
    min_x = min(all_x)
    max_x = max(all_x)
    min_y = min(all_y)
    max_y = max(all_y)

    # Create bounding box corners
    # Note: In OCR coordinates, y=0 is at bottom, so:
    # - top_left has highest y value
    # - bottom_left has lowest y value
    return {
        "tl": {"x": min_x, "y": max_y},  # top-left: min x, max y
        "tr": {"x": max_x, "y": max_y},  # top-right: max x, max y
        "bl": {"x": min_x, "y": min_y},  # bottom-left: min x, min y
        "br": {"x": max_x, "y": min_y},  # bottom-right: max x, min y
    }


def handler(_event, _context):
    """Handle EventBridge scheduled event to generate address similarity cache.

    Args:
        _event: EventBridge event (can be empty dict, unused)
        _context: Lambda context (unused)

    Returns:
        dict: Status of cache generation
    """
    logger.info("Starting address similarity cache generation")

    # Create temporary directory for ChromaDB snapshot
    temp_dir = tempfile.mkdtemp()

    try:
        # Initialize clients
        dynamo_client = DynamoClient(DYNAMODB_TABLE_NAME)

        # Download ChromaDB snapshot from S3
        logger.info(
            "Downloading ChromaDB snapshot from S3: %s/lines", CHROMADB_BUCKET
        )
        download_result = download_snapshot_atomic(
            bucket=CHROMADB_BUCKET,
            collection="lines",
            local_path=temp_dir,
            verify_integrity=True,
        )

        if download_result.get("status") != "downloaded":
            logger.error("Failed to download snapshot: %s", download_result)
            return {
                "statusCode": 500,
                "body": json.dumps(
                    {"error": "Failed to download ChromaDB snapshot"}
                ),
            }

        logger.info(
            "ChromaDB snapshot downloaded: version_id=%s, local_path=%s",
            download_result.get("version_id"),
            temp_dir,
        )

        # Initialize ChromaDB client in read mode from downloaded snapshot
        chroma_client = ChromaDBClient(
            persist_directory=temp_dir,
            mode="read",
        )

        # Verify that the "lines" collection exists in the downloaded snapshot
        logger.info("Checking if 'lines' collection exists in snapshot")
        available_collections = chroma_client.list_collections()
        if "lines" not in available_collections:
            logger.error(
                "Collection 'lines' not found in snapshot. "
                "Available collections: %s",
                available_collections,
            )
            return {
                "statusCode": 500,
                "body": json.dumps(
                    {
                        "error": (
                            "Collection 'lines' not found in ChromaDB snapshot. "
                            f"Available collections: {available_collections}"
                        )
                    }
                ),
            }
        logger.info(
            "Collection 'lines' found. Total collections: %d",
            len(available_collections),
        )

        # Step 1: Get random word with address label
        # Note: The label is "ADDRESS_LINE" per
        # receipt_label/constants.py, not "address"
        logger.info("Fetching address labels")
        address_labels, _ = dynamo_client.get_receipt_word_labels_by_label(
            "ADDRESS_LINE", limit=100
        )

        if not address_labels:
            logger.warning("No address labels found")
            return {
                "statusCode": 200,
                "body": json.dumps({"message": "No address labels found"}),
            }

        # Select random address label
        selected_label = random.choice(address_labels)
        logger.info(
            (
                "Selected address label: image_id=%s, receipt_id=%s, "
                "line_id=%s, word_id=%s"
            ),
            selected_label.image_id,
            selected_label.receipt_id,
            selected_label.line_id,
            selected_label.word_id,
        )

        # Step 2: Get original receipt context
        logger.info("Loading original receipt context")
        original_receipt = dynamo_client.get_receipt_details(
            selected_label.image_id,
            selected_label.receipt_id,
        )

        # Find all address labels in the original receipt
        original_lines = original_receipt.lines
        original_words = original_receipt.words
        original_receipt_labels = [
            label
            for label in original_receipt.labels
            if label.label.upper() == "ADDRESS_LINE"
        ]

        # Group address labels by line_id
        address_line_ids = {label.line_id for label in original_receipt_labels}

        # Find consecutive groups of address lines (addresses are usually consecutive)
        # Sort line_ids to find consecutive ranges
        sorted_line_ids = sorted(address_line_ids)

        # Group consecutive line_ids together
        address_groups = []
        if sorted_line_ids:
            current_group = [sorted_line_ids[0]]
            for i in range(1, len(sorted_line_ids)):
                # If this line_id is consecutive with the previous one, add to current group
                if sorted_line_ids[i] == sorted_line_ids[i-1] + 1:
                    current_group.append(sorted_line_ids[i])
                else:
                    # Start a new group
                    address_groups.append(current_group)
                    current_group = [sorted_line_ids[i]]
            address_groups.append(current_group)

        # Select the group containing the selected label's line_id
        selected_group = None
        for group in address_groups:
            if selected_label.line_id in group:
                selected_group = group
                break

        # Fallback to first group if selected label not found in any group
        if not selected_group and address_groups:
            selected_group = address_groups[0]
            logger.warning(
                "Selected label line_id %d not in any address group, using first group: %s",
                selected_label.line_id,
                selected_group,
            )

        # If no groups found, fallback to just the selected label's line
        if not selected_group:
            selected_group = [selected_label.line_id]
            logger.warning(
                "No address groups found, using selected label line_id: %d",
                selected_label.line_id,
            )

        # Get lines and words for the selected address group
        address_context_lines = [
            line
            for line in original_lines
            if line.line_id in selected_group
        ]
        address_context_words = [
            word
            for word in original_words
            if word.line_id in selected_group
        ]

        # Filter labels to only those in the selected group
        original_receipt_labels = [
            label
            for label in original_receipt_labels
            if label.line_id in selected_group
        ]

        logger.info(
            "Selected address group: line_ids=%s, line_count=%d, word_count=%d, label_count=%d",
            selected_group,
            len(address_context_lines),
            len(address_context_words),
            len(original_receipt_labels),
        )

        # Step 3: Construct line ID and get embedding from ChromaDB
        # Line ID format:
        # IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}
        line_id = (
            f"IMAGE#{selected_label.image_id}#"
            f"RECEIPT#{selected_label.receipt_id:05d}#"
            f"LINE#{selected_label.line_id:05d}"
        )

        logger.info("Fetching embedding for line ID: %s", line_id)

        # Get the line's embedding from ChromaDB
        line_data = chroma_client.get_by_ids(
            collection_name="lines",
            ids=[line_id],
            include=["embeddings", "metadatas", "documents"],
        )

        # Log what we got back for debugging
        logger.info(
            "ChromaDB get_by_ids returned: ids=%s, embeddings_type=%s",
            line_data.get("ids", []),
            type(line_data.get("embeddings")),
        )

        # Check if the ID was found
        ids = line_data.get("ids", [])
        if not ids or line_id not in ids:
            logger.error(
                "Line ID not found in ChromaDB: %s. Available IDs: %s",
                line_id,
                ids[:10] if ids else "none",
            )
            return {
                "statusCode": 500,
                "body": json.dumps(
                    {"error": f"Line ID not found in ChromaDB: {line_id}"}
                ),
            }

        # Check if embeddings exist and are not empty
        # NumPy arrays don't support direct truthiness checks
        embeddings_raw = line_data.get("embeddings", [])
        # Convert to list if it's a NumPy array or other array-like object
        # ChromaDB may return embeddings as NumPy arrays or lists
        if embeddings_raw is None:
            embeddings = []
        elif hasattr(embeddings_raw, "__len__") and not isinstance(
            embeddings_raw, (list, tuple)
        ):
            # It's likely a NumPy array - convert to list
            try:
                # Handle both 1D arrays (single embedding) and 2D arrays
                # (list of embeddings)
                if (
                    hasattr(embeddings_raw, "shape")
                    and len(embeddings_raw.shape) == 1
                ):
                    # Single embedding array, wrap in list
                    embeddings = [embeddings_raw]
                else:
                    # Multi-dimensional array or list-like, convert to list
                    embeddings = list(embeddings_raw)
            except Exception:
                embeddings = []
        else:
            embeddings = (
                embeddings_raw if isinstance(embeddings_raw, list) else []
            )

        if (
            len(embeddings) == 0
            or embeddings[0] is None
            or (hasattr(embeddings[0], "size") and embeddings[0].size == 0)
        ):
            logger.error(
                "Line embedding not found in ChromaDB for ID: %s", line_id
            )
            return {
                "statusCode": 500,
                "body": json.dumps(
                    {"error": "Line embedding not found in ChromaDB"}
                ),
            }

        query_embedding = embeddings[0]
        logger.info(
            "Retrieved embedding from ChromaDB (dimension: %d)",
            len(query_embedding),
        )

        # Step 4: Query ChromaDB for similar lines using the
        # retrieved embedding
        logger.info("Querying ChromaDB for similar lines")
        similar_results = chroma_client.query(
            collection_name="lines",
            query_embeddings=[query_embedding],
            n_results=8,
            include=["metadatas", "distances", "documents"],
        )

        similar_lines_data = similar_results.get("metadatas", [[]])[0]
        similar_distances = similar_results.get("distances", [[]])[0]
        similar_documents = similar_results.get("documents", [[]])[0]

        # Step 5: Process similar lines and get receipt context
        similar_receipts = []
        seen_receipts = set()  # Avoid duplicates

        for metadata, distance, _document in zip(
            similar_lines_data, similar_distances, similar_documents
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

            # Skip if we've already processed this receipt
            receipt_key = (image_id, receipt_id)
            if receipt_key in seen_receipts:
                continue
            seen_receipts.add(receipt_key)

            # Skip if it's the same receipt as the original
            if (
                image_id == selected_label.image_id
                and receipt_id == selected_label.receipt_id
            ):
                continue

            try:
                # Get receipt details
                similar_receipt = dynamo_client.get_receipt_details(
                    image_id, receipt_id
                )

                # Find address labels in this receipt
                similar_labels = [
                    label
                    for label in similar_receipt.labels
                    if label.label.upper() == "ADDRESS_LINE"
                ]

                if not similar_labels:
                    continue

                # Group address labels by line_id and find consecutive groups
                similar_address_line_ids = {
                    label.line_id for label in similar_labels
                }

                # Find consecutive groups of address lines
                sorted_similar_line_ids = sorted(similar_address_line_ids)
                similar_address_groups = []
                if sorted_similar_line_ids:
                    current_group = [sorted_similar_line_ids[0]]
                    for i in range(1, len(sorted_similar_line_ids)):
                        if sorted_similar_line_ids[i] == sorted_similar_line_ids[i-1] + 1:
                            current_group.append(sorted_similar_line_ids[i])
                        else:
                            similar_address_groups.append(current_group)
                            current_group = [sorted_similar_line_ids[i]]
                    similar_address_groups.append(current_group)

                # Select the first (or largest) address group
                # This handles cases where an address appears twice on a receipt
                if similar_address_groups:
                    # Use the largest group, or first if all same size
                    selected_similar_group = max(similar_address_groups, key=len)
                    logger.debug(
                        "Selected address group for similar receipt: line_ids=%s (from %d groups)",
                        selected_similar_group,
                        len(similar_address_groups),
                    )
                else:
                    # Fallback: use all address line_ids
                    selected_similar_group = list(similar_address_line_ids)
                    logger.warning(
                        "No consecutive groups found, using all address line_ids: %s",
                        selected_similar_group,
                    )

                similar_address_lines = [
                    line
                    for line in similar_receipt.lines
                    if line.line_id in selected_similar_group
                ]
                similar_address_words = [
                    word
                    for word in similar_receipt.words
                    if word.line_id in selected_similar_group
                ]

                # Filter labels to only those in the selected group
                similar_labels = [
                    label
                    for label in similar_labels
                    if label.line_id in selected_similar_group
                ]

                # Calculate bounding box for similar address lines
                similar_bbox = calculate_bounding_box_for_lines(similar_address_lines)

                similar_receipts.append(
                    {
                        "receipt": dict(similar_receipt.receipt),
                        "lines": [
                            dict(line) for line in similar_address_lines
                        ],
                        "words": [
                            dict(word) for word in similar_address_words
                        ],
                        "labels": [dict(label) for label in similar_labels],
                        "similarity_distance": float(distance),
                        "bbox": similar_bbox,
                    }
                )

            except Exception as e:  # pylint: disable=broad-exception-caught
                logger.warning(
                    (
                        "Failed to process similar receipt: "
                        "image_id=%s, receipt_id=%s, error=%s"
                    ),
                    image_id,
                    receipt_id,
                    str(e),
                )
                continue

        # Calculate bounding box for original address lines
        original_bbox = calculate_bounding_box_for_lines(address_context_lines)

        # Step 6: Build response structure
        response_data = {
            "original": {
                "receipt": dict(original_receipt.receipt),
                "lines": [dict(line) for line in address_context_lines],
                "words": [dict(word) for word in address_context_words],
                "labels": [dict(label) for label in original_receipt_labels],
                "bbox": original_bbox,
            },
            "similar": similar_receipts,
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
            (
                "Cache generation complete: original_receipt_id=%s, "
                "similar_count=%d"
            ),
            selected_label.receipt_id,
            len(similar_receipts),
        )

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "message": "Cache generated successfully",
                    "original_receipt_id": selected_label.receipt_id,
                    "similar_count": len(similar_receipts),
                }
            ),
        }

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error generating cache: %s", e, exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }
    finally:
        # Cleanup temporary directory
        try:
            shutil.rmtree(temp_dir)
            logger.info("Cleaned up temporary directory: %s", temp_dir)
        except (
            Exception
        ) as cleanup_error:  # pylint: disable=broad-exception-caught
            logger.warning(
                "Failed to cleanup temp directory: %s", cleanup_error
            )
